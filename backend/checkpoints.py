"""LangGraph saver using the SAME SQLAlchemy database as all application data.

Full snapshots (not delta channels), typed msgpack serialization, no pickle.
This supports SQLite locally and PostgreSQL after the configured database cutover.
"""
import asyncio

from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple, WRITES_IDX_MAP
from sqlalchemy import delete

from backend.database import transaction
from backend.records import GraphCheckpoint, GraphWrite


class DatabaseSaver(BaseCheckpointSaver):
    def get_tuple(self, config):
        values = config["configurable"]
        with transaction() as db:
            query = db.query(GraphCheckpoint).filter_by(thread_id=values["thread_id"], namespace=values.get("checkpoint_ns", ""))
            if values.get("checkpoint_id"):
                query = query.filter_by(checkpoint_id=values["checkpoint_id"])
            row = query.order_by(GraphCheckpoint.checkpoint_id.desc()).first()
            if not row:
                return None
            writes = db.query(GraphWrite).filter_by(thread_id=row.thread_id, namespace=row.namespace, checkpoint_id=row.checkpoint_id).order_by(GraphWrite.task_path, GraphWrite.task_id, GraphWrite.idx).all()
            saved = {"configurable": {"thread_id": row.thread_id, "checkpoint_ns": row.namespace, "checkpoint_id": row.checkpoint_id}}
            parent = {"configurable": {**saved["configurable"], "checkpoint_id": row.parent_id}} if row.parent_id else None
            return CheckpointTuple(saved, self.serde.loads_typed((row.payload_type, bytes(row.payload))),
                self.serde.loads_typed((row.metadata_type, bytes(row.metadata_blob))), parent,
                [(w.task_id, w.channel, self.serde.loads_typed((w.payload_type, bytes(w.payload)))) for w in writes])

    def put(self, config, checkpoint, metadata, new_versions):
        values = config["configurable"]
        kind, payload = self.serde.dumps_typed(checkpoint)
        meta_kind, meta_payload = self.serde.dumps_typed(metadata)
        with transaction() as db:
            db.merge(GraphCheckpoint(thread_id=values["thread_id"], namespace=values.get("checkpoint_ns", ""),
                checkpoint_id=checkpoint["id"], parent_id=values.get("checkpoint_id"), payload_type=kind, payload=payload,
                metadata_type=meta_kind, metadata_blob=meta_payload))
        return {"configurable": {"thread_id": values["thread_id"], "checkpoint_ns": values.get("checkpoint_ns", ""), "checkpoint_id": checkpoint["id"]}}

    def put_writes(self, config, writes, task_id, task_path=""):
        values = config["configurable"]
        with transaction() as db:
            for index, (channel, value) in enumerate(writes):
                index = WRITES_IDX_MAP.get(channel, index)
                key = (values["thread_id"], values.get("checkpoint_ns", ""), values["checkpoint_id"], task_id, index)
                existing = db.get(GraphWrite, key)
                if existing and index >= 0:
                    continue
                kind, payload = self.serde.dumps_typed(value)
                db.merge(GraphWrite(thread_id=key[0], namespace=key[1], checkpoint_id=key[2], task_id=task_id,
                    idx=index, channel=channel, task_path=task_path, payload_type=kind, payload=payload))

    def list(self, config, *, filter=None, before=None, limit=None):
        with transaction() as db:
            query = db.query(GraphCheckpoint)
            if config:
                values = config["configurable"]
                query = query.filter_by(thread_id=values["thread_id"])
                if "checkpoint_ns" in values:
                    query = query.filter_by(namespace=values["checkpoint_ns"])
            if before:
                query = query.filter(GraphCheckpoint.checkpoint_id < before["configurable"]["checkpoint_id"])
            rows = query.order_by(GraphCheckpoint.checkpoint_id.desc()).all()
        count = 0
        for row in rows:
            if limit is not None and count >= limit:
                break
            checkpoint = self.get_tuple({"configurable": {"thread_id": row.thread_id, "checkpoint_ns": row.namespace, "checkpoint_id": row.checkpoint_id}})
            if checkpoint and (not filter or all(checkpoint.metadata.get(k) == v for k, v in filter.items())):
                yield checkpoint
                count += 1

    def delete_thread(self, thread_id):
        with transaction() as db:
            db.execute(delete(GraphWrite).where(GraphWrite.thread_id == thread_id))
            db.execute(delete(GraphCheckpoint).where(GraphCheckpoint.thread_id == thread_id))

    async def aget_tuple(self, config):
        return await asyncio.to_thread(self.get_tuple, config)

    async def aput(self, config, checkpoint, metadata, new_versions):
        return await asyncio.to_thread(self.put, config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path=""):
        await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)

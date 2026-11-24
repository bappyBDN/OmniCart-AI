from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from backend import llm


def test_models_bind_tools_before_fallbacks(monkeypatch):
    bindings = []

    class Provider:
        def __init__(self, name): self.name = name

        def bind_tools(self, tools):
            bindings.append((self.name, tools))

            def invoke(messages):
                if self.name == 'primary':
                    raise RuntimeError('provider unavailable')
                return AIMessage(content='fallback succeeded')

            return RunnableLambda(invoke)

    monkeypatch.setattr(llm, 'configured_models', lambda vision: [Provider('primary'), Provider('backup')])
    model = llm.chat_model(tools=['tool-schema'])
    assert model.invoke([]).content == 'fallback succeeded'
    assert bindings == [('primary', ['tool-schema']), ('backup', ['tool-schema'])]


def test_background_warm_uses_same_cache_key_as_requests(monkeypatch):
    called = []
    monkeypatch.setattr(llm, 'configured_models', lambda vision: called.append(vision) or ['model'])
    monkeypatch.setattr(llm, 'model_status', 'cold')
    llm.warm_models()
    assert called == [False]
    assert llm.model_status == 'ready'

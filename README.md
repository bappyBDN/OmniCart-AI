
# 🛒 OmniCart AI
**Vibe to Production in 180 Seconds | Revolutionizing E-commerce Support & Automation in Bangladesh**

![OmniCart AI Architecture](link-to-your-architecture-image.png)
*(Note: Replace the link above with the actual GitHub link to your Figma/Eraser architecture image)*

---

## 🚀 The Problem (The Vibe)
Small and medium online businesses (SMEs) face a massive bottleneck: they cannot afford 24/7 human customer support. 
* **Overwhelmed Inboxes:** Delayed replies during busy periods or off-hours lead to customer drop-offs.
* **"Dumb" Chatbots:** Existing bots forget context, cannot execute real database orders, and fail to answer complex policy questions.
* **Language Barrier:** Lack of native Bangla AI support restricts local businesses.
* **Inventory Hallucination:** Manual stock checking causes severe delays.

## 💡 The Solution
**OmniCart AI** acts as a smart, 24/7 virtual worker for online shops. Built using a **LangGraph-powered Multi-Agent System**, it doesn't just chat—it executes. It routes queries, checks live SQL databases, reads vector-stored policies via RAG, and allows real-time human intervention.

---

## ✨ Key Features & The Multi-Agent Swarm

🧠 **Smart Orchestrator (LangGraph):** Evaluates user intent (Text/Image) in English & Bangla and intelligently routes the task to the right expert agent.
🛒 **Sales Agent (SQL Tool Calling):** Checks live SQLite database for real-time inventory, prevents hallucination, and safely executes orders.
🎧 **Support Agent (RAG Pipeline):** Queries a ChromaDB vector store to provide accurate answers regarding complex return policies, shipping, and FAQs.
👁️ **Vision Agent:** Analyzes user-uploaded product images to extract context and identify exact products.
👨‍💻 **Human-in-the-Loop (HITL):** A complete Admin Dashboard with real-time SQLite sync. Admins can view conversation history and click **"Take Over Chat"** to disable AI and reply directly to the customer.
📧 **Automated Triggers:** Instantly fires SMTP emails for order confirmations.
🛡️ **Unbreakable Resilience:** Built-in Regex Firewall for prompt injection security. Features robust API key rotation and an automated HuggingFace (Qwen) fallback if the primary LLM rate limits.

---

## 🛠️ Tech Stack
* **Framework:** Streamlit (UI & Admin Dashboard)
* **Orchestration:** LangGraph & LangChain
* **LLMs:** * Groq (Llama-3.1-8B for routing, Llama-3.3-70B for Tool Calling)
  * HuggingFace Fallback (Qwen2.5-7B)
  * Vision: Llama-3.2-11B-Vision
* **Databases:** SQLite (Relational DB) & ChromaDB (Vector Store)
* **Language Support:** Native Bangla & English

---

## ⚙️ How to Run Locally

### 1. Clone the repository
```bash
git clone [https://github.com/your-username/OmniCart-AI.git](https://github.com/your-username/OmniCart-AI.git)
cd OmniCart-AI

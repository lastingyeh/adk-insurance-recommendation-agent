# Enterprise Insurance Agent：從推薦到核保通知

## 1. 整體架構

```text
┌─────────────────────────────────────────────────────────────────────┐
│                         CUSTOMER EXPERIENCE                         │
│                                                                     │
│                Next.js / Web / Mobile / Chat                       │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         API / SESSION LAYER                         │
│                                                                     │
│                  FastAPI + Auth + Session + SSE/WS                  │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
╔═════════════════════════════════════════════════════════════════════╗
║                     GOOGLE ADK AGENT RUNTIME                       ║
║                                                                     ║
║  ┌──────────────────────────────────────────────────────────────┐  ║
║  │                 Recommendation Agent                         │  ║
║  │                                                              │  ║
║  │ Understand Customer → Generate Candidate Products            │  ║
║  └──────────────────────────────┬───────────────────────────────┘  ║
║                                 │                                  ║
║                                 ▼                                  ║
║  ┌──────────────────────────────────────────────────────────────┐  ║
║  │                 Consensus Agent                              │  ║
║  │                                                              │  ║
║  │       Model A ─┐                                             │  ║
║  │       Model B ─┼──→ Consensus Voting → Score                │  ║
║  │       Model C ─┤                                             │  ║
║  │       Model D ─┘                                             │  ║
║  └──────────────────────────────┬───────────────────────────────┘  ║
║                                 │                                  ║
║                                 ▼                                  ║
║  ┌──────────────────────────────────────────────────────────────┐  ║
║  │                    Harness Gate                              │  ║
║  │                                                              │  ║
║  │ Policy │ Eligibility │ Risk │ Schema │ Guardrail │ Audit      │  ║
║  └──────────────────────────────┬───────────────────────────────┘  ║
║                                 │                                  ║
║                          PASS / ESCALATE                           ║
║                                 │                                  ║
║                                 ▼                                  ║
║  ┌──────────────────────────────────────────────────────────────┐  ║
║  │                 Underwriting Agent                           │  ║
║  │                                                              │  ║
║  │ Risk Assessment → Rule Check → Decision                      │  ║
║  └──────────────────────────────┬───────────────────────────────┘  ║
║                                 │                                  ║
║                                 ▼                                  ║
║  ┌──────────────────────────────────────────────────────────────┐  ║
║  │                 Notification Agent                           │  ║
║  │                                                              │  ║
║  │ Email / App / SMS / Letter                                   │  ║
║  └──────────────────────────────────────────────────────────────┘  ║
╚═════════════════════════════════════════════════════════════════════╝
                               │
                               │ MCP
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         MCP TOOLBOX                                 │
│                                                                     │
│  Customer Tool       Insurance Product Tool       Rules Tool        │
│       │                       │                       │             │
│       ├───────────────────────┼───────────────────────┤             │
│       │                       │                       │             │
│       ▼                       ▼                       ▼             │
│ Customer Profile        Product Catalog       Insurance Rules       │
│ Customer History        Coverage              Eligibility Rules      │
│ Existing Policies       Pricing               Underwriting Rules     │
│                                                                     │
│                 Vector FAQ / Knowledge Search                       │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                            DATA                                    │
│                                                                     │
│ PostgreSQL + pgvector │ Redis │ Object Storage │ Audit / Trace       │
└─────────────────────────────────────────────────────────────────────┘


═══════════════════════════════════════════════════════════════════════
                     HARNESS CONTROL PLANE
═══════════════════════════════════════════════════════════════════════

 Policy Enforcement │ Evaluation │ Observability │ Audit │ Security
 Human Escalation    │ Trace      │ Metrics       │ PII   │ Feedback
```

---

# 2. 每一層到底負責什麼？

這裡是整個 Enterprise Agent 最重要的設計。

## Google ADK：Agent Orchestration

Google ADK 不應該直接承擔所有企業規則。

它主要負責：

```text
Understand
   ↓
Plan
   ↓
Select Agent
   ↓
Call Tool
   ↓
Reason
   ↓
Decide Next Action
```

例如：

```text
Recommendation Agent
        │
        ├── Customer Profile Tool
        │
        ├── Product Search Tool
        │
        ├── Insurance Rules Tool
        │
        └── Consensus Agent
```

ADK 是：

> **Agent Brain / Orchestration Runtime**

但不是整個企業治理層。

---

# 3. MCP Toolbox：把企業能力變成 Tools

MCP Toolbox 的角色非常關鍵。

不要讓 Agent 直接操作 Database。

而是：

```text
Agent
  │
  ▼
MCP Toolbox
  │
  ├── get_customer_profile()
  ├── search_insurance_products()
  ├── check_product_eligibility()
  ├── search_insurance_rules()
  ├── assess_underwriting_rule()
  └── search_faq_knowledge()
```

例如 Agent 說：

```text
search_insurance_products(
    customer_profile,
    coverage_need,
    budget
)
```

MCP Toolbox 再負責：

```text
Tool
 ↓
PostgreSQL
 ↓
Insurance Product
 ↓
Return Structured Data
```

這會讓：

**Agent Reasoning**

與

**Enterprise Data Access**

真正解耦。

---

# 4. Insurance Rules：不要讓 LLM「猜規則」

這是保險系統最重要的一層。

例如：

```text
Product: Medical Plan A

Eligibility:
Age >= 20
Age <= 65

Coverage:
Hospitalization <= X
Critical Illness <= Y

Underwriting:
High Risk → Manual Review
Missing Information → Request More Data
Rule Conflict → Reject / Escalate
```

不要把這些全部放進 Prompt：

```text
❌ Prompt:
「請記住所有保險規則並進行判斷」
```

而應該：

```text
Agent
   │
   ▼
MCP Toolbox
   │
   ▼
Insurance Rules
   │
   ▼
Structured Result
   │
   ▼
Agent
```

例如：

```json
{
  "eligible": true,
  "risk_level": "medium",
  "requires_manual_review": false,
  "violated_rules": []
}
```

LLM 負責理解與推理。

**Rules Engine 負責真實世界的約束。**

---

# 5. Consensus Voting：可靠性的第一道防線

假設 Recommendation Agent 找到：

```text
Product A
Product B
Product C
```

不要立即決定。

而是：

```text
             Candidate Product A
                     │
       ┌─────────────┼─────────────┐
       ▼             ▼             ▼
    Model A        Model B       Model C
       │             │             │
       ▼             ▼             ▼
    Fit: 0.91     Fit: 0.86     Fit: 0.88
    Risk: 0.80    Risk: 0.84    Risk: 0.82
       │             │             │
       └─────────────┼─────────────┘
                     ▼
             Consensus Engine
                     │
                     ▼
                Score = 0.87
```

這裡的核心思想：

> **Independent models provide evidence; the system makes the decision.**

因此你不是單純：

**LLM → Answer**

而是：

**Models → Evidence → Consensus → Decision**

---

# 6. Harness Engineering：真正的核心

這一層我認為最值得你在技術文章中強調。

可以把 Harness 定義成：

> **The Harness is the control plane that makes Agent behavior observable, testable, constrainable and recoverable.**

它至少包含：

### Policy Guard

```text
Product eligibility
Risk policy
Underwriting policy
Regulatory constraints
```

### Output Validation

```text
Schema validation
Required fields
Allowed values
Consistency
```

### Consensus Gate

```text
Score >= threshold
        ↓
      PASS

Score < threshold
        ↓
    ESCALATION
```

### Human Gate

```text
High Risk
   OR
Low Consensus
   OR
Rule Conflict
   ↓
Human Review
```

### Audit

記錄：

```text
Customer Input
Model Outputs
Consensus Score
Rules Retrieved
Tools Called
Agent Decision
Human Decision
Final Underwriting Result
Notification
```

這樣未來才能回答：

> 「為什麼這個客戶最後被核保拒絕？」

而不是：

> 「LLM 當時好像是這樣回答的。」

---

# 7. 完整 Business Flow

現在把整個系統串起來。

### Step 1 — Customer Request

```text
「我每月預算 3,000 元，
想增加醫療保障。」
```

↓

### Step 2 — Recommendation Agent

Google ADK 理解：

```text
Budget
Coverage Need
Customer Profile
Existing Policy
```

↓

### Step 3 — MCP Toolbox

查詢：

```text
Customer Profile
Existing Insurance
Insurance Products
Insurance Rules
FAQ / Knowledge
```

↓

### Step 4 — Candidate Generation

產生：

```text
Product A
Product B
Product C
```

↓

### Step 5 — Consensus Voting

3～5 個模型獨立評估。

↓

### Step 6 — Harness

檢查：

```text
✓ Product Eligibility
✓ Rule Compliance
✓ Risk
✓ Data Completeness
✓ Output Schema
✓ Consensus Threshold
```

↓

### Step 7 — Human Escalation

如果：

```text
Consensus < threshold
```

或：

```text
High Risk
Rule Conflict
Missing Information
```

直接：

**Human Review**

↓

### Step 8 — Underwriting Agent

通過 Gate 後才進入：

```text
Risk Assessment
       ↓
Policy Check
       ↓
Underwriting Decision
```

↓

### Step 9 — Notification Agent

產生：

```text
Approved
Conditionally Approved
Additional Information Required
Manual Review
Rejected
```

↓

### Step 10 — Harness Final Validation

最後一次檢查：

```text
Is notification consistent
with underwriting decision?
```

↓

### Step 11 — Customer

才真正收到：

```text
Email
App
SMS
Letter
```

---

# 8. 最重要的 Sequence Diagram

```text
Customer
   │
   │ Insurance Request
   ▼
Recommendation Agent
   │
   │ get_customer_profile()
   ▼
MCP Toolbox
   │
   ▼
PostgreSQL
   │
   │ Customer Data
   ▼
Recommendation Agent
   │
   │ search_products()
   ▼
MCP Toolbox
   │
   ▼
Insurance Products
   │
   ▼
Recommendation Agent
   │
   │ Candidate Products
   ▼
Consensus Agent
   │
   ├──────── Model A
   ├──────── Model B
   ├──────── Model C
   ├──────── Model D
   └──────── Model E
   │
   │ Consensus Score
   ▼
Harness
   │
   ├── Policy Validation
   ├── Eligibility
   ├── Risk
   ├── Schema
   └── Guardrails
   │
   ├──────── FAIL ───────► Human
   │
   ▼ PASS
Underwriting Agent
   │
   │ check_underwriting_rules()
   ▼
MCP Toolbox
   │
   ▼
Insurance Rules
   │
   ▼
Underwriting Agent
   │
   │ Decision
   ▼
Harness
   │
   │ Final Validation
   ▼
Notification Agent
   │
   ▼
Customer
```

---

# 9. 這套架構真正解決什麼？

它不是單純增加 Agent 數量。

而是把風險拆開：

| 風險              | 解法                    |
| ----------------- | ----------------------- |
| LLM hallucination | Consensus               |
| 單一模型偏差      | Multi-model voting      |
| 錯誤產品推薦      | Insurance Rules         |
| 不符合資格        | Eligibility Gate        |
| 高風險案件        | Human Escalation        |
| Agent 行為不可控  | Harness                 |
| 無法追溯          | Audit / Trace           |
| 資料品質問題      | Data Validation         |
| 錯誤通知          | Final Output Validation |

所以整個架構形成：

```text
          MODEL RELIABILITY
                 │
                 ▼
        ┌────────────────┐
        │ Consensus Vote │
        └───────┬────────┘
                │
                ▼
          BUSINESS RULES
                │
                ▼
        ┌────────────────┐
        │    Harness     │
        └───────┬────────┘
                │
                ▼
        HUMAN SUPERVISION
                │
                ▼
          UNDERWRITING
                │
                ▼
           CUSTOMER
```

這就是我會建議你對外定位的核心：

> **We don't make the Agent reliable by trusting a better model.
> We make the system reliable by designing for model failure.**

---

# 10. 最終 Enterprise Architecture 定位

如果把你的整套技術能力濃縮成一張圖，我會把角色定義成：

```text
Google ADK
     │
     │ Orchestration
     ▼
Agentic Workflow
     │
     ├───────────────┐
     ▼               ▼
MCP Toolbox       Consensus
     │               │
Enterprise Data   Model Reliability
     │               │
     └───────┬───────┘
             ▼
       Harness Layer
             │
    ┌────────┼────────┐
    ▼        ▼        ▼
 Policy    Eval     Guardrail
    │        │        │
    └────────┼────────┘
             ▼
       Human Escalation
             │
             ▼
        Underwriting
             │
             ▼
        Notification
             │
             ▼
          Customer
```

**這樣就不再是一個「Insurance Recommendation Demo」。**

而是一個完整的：

### **Enterprise Insurance Decision Agent**

其中：

* **Google ADK** → Agent orchestration
* **MCP Toolbox** → Enterprise capability / tool abstraction
* **Insurance Rules** → Deterministic business constraints
* **Consensus Voting** → Model-level reliability
* **Harness Engineering** → System-level reliability
* **Human-in-the-Loop** → Governance
* **Underwriting Agent** → Business execution
* **Audit / Observability** → Production accountability

這個架構非常適合進一步做成你的 **AI Solution Architect / Technical Evangelist** 技術案例：不是展示「我會使用 Google ADK」，而是展示**如何把 ADK、MCP、Multi-Model Consensus、Harness Engineering 與企業保險流程整合成一個可治理、可觀測、可逐步自動化的 Production Agent System**。

# Cost Analysis — Enterprise Agentic RAG Platform

## Assumptions (Baseline Deployment)

| Parameter | Value |
|-----------|-------|
| Documents in corpus | 10,000 documents |
| Avg pages per document | 15 pages |
| Avg chunks per page | 4 (parent+child) |
| Total chunks | ~600,000 |
| Avg chunk tokens | 256 (child) / 1024 (parent) |
| Daily active users | 500 |
| Queries per user per day | 10 |
| Total daily queries | 5,000 |

---

## 1. Ingestion Costs (One-Time)

### Embedding Generation (10,000 documents)

| Model | Cost per 1K tokens | Total tokens | Cost |
|-------|-------------------|--------------|------|
| OpenAI text-embedding-3-large | $0.00013 | ~120M tokens | **$15.60** |
| BGE-M3 (self-hosted) | ~$0.00005 | ~120M tokens | **$6.00** (compute only) |
| E5-Large (self-hosted) | ~$0.00005 | ~120M tokens | **$6.00** (compute only) |

### LLM Metadata Enrichment (10,000 documents)

Using Claude Haiku 4.5 (small LLM):
- Input: 1,000 tokens/doc × 10,000 = 10M tokens
- Output: 200 tokens/doc × 10,000 = 2M tokens
- Claude Haiku pricing: $0.80/M input, $4.00/M output
- Cost: **(10 × $0.80) + (2 × $4.00) = $8 + $8 = $16.00**

**Total One-Time Ingestion Cost: ~$32–$50**

---

## 2. Daily Query Costs (5,000 queries/day)

### Query Rewriting (Small LLM — Claude Haiku 4.5)
Per query:
- Input: ~200 tokens (system prompt + query)
- Output: ~300 tokens (rewritten + expanded)
- 5,000 queries × 500 tokens avg = 2.5M tokens/day

Cost: **2.5M × $0.80/M input + 1.5M × $4.00/M output ≈ $2.00 + $6.00 = $8.00/day**

### Context Compression (Small LLM — Claude Haiku 4.5)
Per query:
- Input: ~3,000 tokens (10 chunks × 300 tokens avg)
- Output: ~800 tokens (compressed)
- 5,000 queries × 3,800 tokens avg = 19M tokens/day

Cost: **15M × $0.80 + 4M × $4.00 ≈ $12.00 + $16.00 = $28.00/day**

### Answer Generation (Large LLM — Claude Sonnet 4.6)
Per query:
- Input: ~4,000 tokens (system + compressed context + query)
- Output: ~500 tokens (answer)
- 5,000 queries × 4,500 tokens avg = 22.5M tokens/day

Sonnet 4.6 pricing: $3.00/M input, $15.00/M output
Cost: **20M × $3.00 + 2.5M × $15.00 = $60.00 + $37.50 = $97.50/day**

### Embedding at Query Time
- 5,000 queries × 5 variants × 256 tokens = ~6.4M tokens/day
- OpenAI: 6.4M × $0.00013 = **$0.83/day**
- With Redis cache (~60% hit rate): $0.83 × 0.4 = **$0.33/day**

### Daily LLM Cost Summary

| Component | Daily Cost |
|-----------|-----------|
| Query rewriting (Haiku) | $8.00 |
| Context compression (Haiku) | $28.00 |
| Answer generation (Sonnet) | $97.50 |
| Embedding generation | $0.33 |
| **Total Daily LLM Cost** | **$133.83/day** |
| **Monthly LLM Cost** | **~$4,015/month** |

**With 60% cache hit rate on repeated queries:**
Estimated real cost: **~$2,400/month**

---

## 3. Infrastructure Costs (Self-Hosted / Cloud)

### Option A: Docker Compose on Single Server
*(Suitable for ≤1,000 DAU)*

| Service | Spec | Monthly Cost (cloud VM) |
|---------|------|------------------------|
| API + Streamlit server | 4 vCPU, 16GB RAM | $120 (AWS t3.xlarge) |
| PostgreSQL | 2 vCPU, 8GB RAM, 100GB SSD | $80 (RDS t3.large) |
| Qdrant | 4 vCPU, 16GB RAM, 200GB SSD | $200 (dedicated) |
| Elasticsearch | 4 vCPU, 16GB RAM, 200GB SSD | $200 (dedicated) |
| Redis | 1 vCPU, 4GB RAM | $30 (ElastiCache t3.small) |
| **Total Infrastructure** | | **$630/month** |

### Option B: Kubernetes (Production Scale)
*(Suitable for ≤10,000 DAU)*

| Service | Spec | Monthly Cost |
|---------|------|-------------|
| API pods (3 replicas) | 2 vCPU, 4GB each | $200 |
| Streamlit pod | 1 vCPU, 2GB | $50 |
| PostgreSQL (managed) | db.r5.large | $200 |
| Qdrant cluster (3 nodes) | 8 vCPU, 32GB each | $800 |
| Elasticsearch cluster | 3 × m5.large | $400 |
| Redis (managed) | cache.r5.large | $100 |
| Load balancer | ALB | $30 |
| **Total Infrastructure** | | **$1,780/month** |

---

## 4. Observability Costs

| Service | Plan | Monthly |
|---------|------|---------|
| Langfuse | Cloud Hobby (≤50k traces) | $0 / month |
| Langfuse | Cloud Pro (≤1M traces) | $59 / month |
| Prometheus + Grafana | Self-hosted | $0 (included in infra) |
| OpenTelemetry | Self-hosted Jaeger | $0 (included in infra) |

At 5,000 queries/day = ~150,000 traces/month → **Langfuse Pro: $59/month**

---

## 5. Optional External Services

| Service | Usage | Monthly |
|---------|-------|---------|
| Cohere Rerank API | 5,000 queries × $0.001 | $5.00 |
| LlamaParse | 10,000 docs × $0.003/page × 15 pages | $450 (one-time) |
| BGE Reranker (self-hosted) | Included in GPU cost | $50 GPU |

---

## 6. Total Cost of Ownership (Monthly)

### Small Deployment (500 DAU, self-hosted)

| Category | Monthly Cost |
|----------|-------------|
| LLM APIs (with caching) | $2,400 |
| Infrastructure (Option A) | $630 |
| Observability | $59 |
| **Total** | **$3,089/month** |

**Cost per query:** $3,089 / 150,000 = **$0.021/query**

### Medium Deployment (2,000 DAU, cloud-native)

| Category | Monthly Cost |
|----------|-------------|
| LLM APIs (with caching, 4× volume) | $7,200 |
| Infrastructure (Option B) | $1,780 |
| Observability | $59 |
| **Total** | **$9,039/month** |

**Cost per query:** $9,039 / 600,000 = **$0.015/query**

---

## 7. Cost Optimization Strategies

| Strategy | Expected Savings |
|----------|-----------------|
| Redis query cache (60% hit rate) | 40% LLM cost reduction |
| Use Haiku instead of Sonnet for simple queries | 60% cost reduction on 30% of queries |
| BGE self-hosted reranker vs Cohere | $150/month saved at scale |
| Prompt caching (Anthropic) | 10-20% input token savings |
| Smaller child chunks (128 tokens) | Fewer tokens per retrieval |
| Batch embedding with cache | 50% embedding cost reduction |
| Semantic deduplication at ingest | Fewer chunks stored/searched |

**Realistic optimized cost for 500 DAU:** ~**$2,100/month** (~32% savings)

---

## 8. Cost Monitoring Alerts

Recommended Prometheus/Langfuse alerts:

| Alert | Threshold | Action |
|-------|-----------|--------|
| Daily LLM spend > $200 | 150% of baseline | Investigate high-usage users |
| Embedding API calls > 10k/hour | Spike detection | Check for ingestion loop bug |
| Cache hit rate < 30% | Below baseline | Cache configuration issue |
| Cost per query > $0.05 | 2.5× baseline | Review compression/caching |

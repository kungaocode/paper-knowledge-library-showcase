# 论文概念向量库 · 研究展示版

从论文中提取带出处的概念，按主概念、子概念和属性组织，再建向量索引供检索。本展示版从 **1,534 篇论文、15,180 条概念**的完整库中选出 **12 篇论文、29 条概念和 1 条附属属性**，覆盖 Transformer、强化学习、RAG、智能体、扩散模型、多模态、世界模型、推理效率、对齐、可信评测及 AI 科学应用。

## 体验检索和出处回查

解压后，在该目录中运行以下命令。使用 Python 3.11 或更新版本及其标准库。

~~~text
python search.py search "FlashAttention 分块" --limit 1
python search.py search "证据检索" --collection recent_ai --limit 1
python search.py outline arxiv:2205.14135
python search.py build
~~~

前两条分别展示注意力分块和文献证据检索。第三条显示论文内的概念树、各项页码和论文链接。最后一条从随包的两份 JSONL 重建 TF-IDF 稀疏向量索引。

## 文件

| 文件 | 内容 |
|---|---|
| `data/papers.jsonl` | 题名、作者、版本化的公开出处及主题 |
| `data/concepts.jsonl` | 样例概念、释义、别名、层级和 PDF 物理页码 |
| `data/index.sqlite3` | 可重建的离线索引 |
| `search.py` | 离线检索、概念树和索引构建工具 |
| [PROCESS.md](PROCESS.md) | 从选文、提取、复核和事务入库到检索及验收的全过程 |
| [RIGHTS.md](RIGHTS.md) | 代码、整理数据和论文出处的许可范围 |

样例使用重新撰写的简短释义。引用学术结论时可通过论文链接和页码核对原文。


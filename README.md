# 语料标注与争议仲裁

项目使用 Python 标准库、SQLite 和 `http.server`，实现批次、指南版本、重复标注、分歧检测、仲裁、一致性指标、金标准冻结与导出，并以“提交前不可查看含答案讨论”的方式隔离讨论区答案。

领取/交回/冻结按约定覆盖人数串联：建条目时登记 `required_annotators`（默认 2）；每位标注员手里未提交的任务最多保留 2 份，条目名额满或个人到上限时领取请求停进 FIFO 等待队列，重复领取幂等（不重新排队、不报错）；交回未提交任务立即释放名额并按 FIFO 补入队列，提交标注也会释放个人手速名额并触发补位。

## 启动

```bash
python app.py
```

默认地址 <http://127.0.0.1:8112>，默认数据库为 `corpus.db`。首次启动会写入两位标注员、一位仲裁员和一个含分歧的示例批次。旧数据库启动时自动补上 `items.required_annotators` 列和 `claim_queue` 表。

```bash
PORT=9002 CORPUS_DB=/tmp/corpus.db python app.py
```

## 测试

```bash
python -m unittest discover -s tests -v
```

测试包括：分配、标注、发现分歧、阻止提前冻结、仲裁、计算一致性、冻结和导出；另验证提交答案前后讨论可见性变化、错误角色不能领取标注任务，以及名额上限、个人手速上限、FIFO 排队/补位、交回取消排队和冻结时逐条覆盖缺口提示。

## 接口

- `POST /api/users`、`POST /api/guidelines`、`POST /api/batches`
- `POST /api/batches/{id}/items`（可带 `required_annotators`，默认 2，至少 1）
- `POST /api/claims`、`POST /api/batches/{id}/assign`（两者等价，领取/排队）
- `POST /api/returns`（交回未提交任务或取消排队，立即补位）
- `POST /api/annotations`、`POST /api/adjudications`
- `GET /api/items/{id}?user_id=`
- `GET /api/batches/{id}/disagreements`
- `GET /api/batches/{id}/coverage`（逐条所需人数、已提交、占住未提交、排队、缺口）
- `GET /api/batches/{id}/consistency`
- `POST /api/batches/{id}/freeze`
- `GET /api/batches/{id}/gold`

领取返回 `state`：`assigned`（直接占入名额）、`submitted`（之前已提交的幂等返回）或 `queued`（停在队列，含 `queue_position`）。交回与提交的返回里带 `promoted`，列出本次 FIFO 补入名额的（条目、标注员）。

一致性同时返回逐条成对一致率和 Fleiss Kappa。冻结要求每条提交人数达到该条目登记的 `required_annotators`、没有未仲裁分歧；不满足时错误信息逐条说明“需几人、仅几人提交、差几人、谁占住未提交、队列几人”。冻结后不能修改标注，导出结果来自不可变的 `gold_records`。`GET /api/state` 的快照包含每人待办数（`pending_count`/`queued_count`）和每条目的已占、已提交、排队、缺口字段，供页面展示。

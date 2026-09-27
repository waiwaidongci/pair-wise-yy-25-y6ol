# 语料标注与争议仲裁

项目使用 Python 标准库、SQLite 和 `http.server`，实现批次、指南版本、重复标注、分歧检测、仲裁、一致性指标、金标准冻结与导出，并以“提交前不可查看含答案讨论”的方式隔离讨论区答案。

## 启动

```bash
python app.py
```

默认地址 <http://127.0.0.1:8112>，默认数据库为 `corpus.db`。首次启动会写入两位标注员、一位仲裁员和一个含分歧的示例批次。

```bash
PORT=9002 CORPUS_DB=/tmp/corpus.db python app.py
```

## 测试

```bash
python -m unittest discover -s tests -v
```

测试包括：分配、标注、发现分歧、阻止提前冻结、仲裁、计算一致性、冻结和导出；另一条测试验证提交答案前后讨论可见性变化，以及错误角色不能领取标注任务。

## 接口

- `POST /api/users`、`POST /api/guidelines`、`POST /api/batches`
- `POST /api/batches/{id}/items`（可带 `required_annotators` 登记所需标注人数，默认 2）、`POST /api/batches/{id}/assign`
- `POST /api/items/{id}/return`（交回未提交的任务）
- `POST /api/annotations`、`POST /api/adjudications`
- `GET /api/items/{id}?user_id=`
- `GET /api/batches/{id}/disagreements`
- `GET /api/batches/{id}/consistency`
- `POST /api/batches/{id}/freeze`
- `GET /api/batches/{id}/gold`

领取、交回与冻结是联动的：建条目时登记所需标注人数；每位标注员手里未提交的任务最多 2 份，条目名额已满或个人待办达上限时领取请求进入等待队列（重复领取保持排队，不重复占位）；交回未提交任务会立即放出名额并按排队顺序补位，提交也会释放个人名额触发补位。`/api/state` 返回每位标注员的待办数、每个条目的已领/已交/还缺人数和等待队列。冻结按条目登记的人数核查覆盖，不足时报出具体缺口（如"条目#3（序号3）需2人，已交0人，还差2人"）。

一致性同时返回逐条成对一致率和 Fleiss Kappa。冻结要求每条达到登记覆盖人数、没有未仲裁分歧；冻结后不能修改标注，导出结果来自不可变的 `gold_records`。

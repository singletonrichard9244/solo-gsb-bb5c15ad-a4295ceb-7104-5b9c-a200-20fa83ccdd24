# 实验室样品分装谱系 API（Sample Aliquot Lineage API）

基于 **Python + FastAPI + SQLite** 的实验室样品分装谱系追踪服务：

- 登记带体积的初始样品（容器初始来源 100% 为该样品）
- 批量提交**依次执行**的容器间转移，每次按源容器**当时的混合比例**把全部祖先来源等比传播到目标容器
- 所有体积与占比均用 `Decimal` 计算（50 位有效精度），并以字符串存储，杜绝浮点误差
- 源液不足、未知容器、自转移时**整批拒绝**，数据库状态保持不变（单事务回滚）
- 查询任一容器的当前体积及各祖先来源占比

## 快速启动（Docker）

```bash
docker compose up --build
```

一条命令即可完成：构建镜像 → 容器启动时安装依赖 → 建库建表 → 启动服务。

- API 地址：http://localhost:8000
- 交互式文档（Swagger UI）：http://localhost:8000/docs
- 健康检查：http://localhost:8000/health

SQLite 数据文件保存在名为 `lineage-data` 的 Docker 卷中（容器内路径 `/data/lineage.db`），删除重建容器数据不丢失。

## 配置项

均通过环境变量配置，可在 `docker-compose.yml` 的 `environment` 段修改：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:////data/lineage.db`（容器）/ `sqlite:///./data/lineage.db`（本地） | SQLAlchemy 数据库连接串。SQLite 文件目录不存在时会自动创建；也支持 `sqlite:///:memory:` |
| `HOST` | `0.0.0.0` | 服务监听地址 |
| `PORT` | `8000` | 服务监听端口（compose 已将宿主机 8000 映射到容器 8000） |

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/samples` | 登记初始样品：创建容器并记录 100% 来源 |
| `POST` | `/transfers` | 批量提交转移，按数组顺序依次执行；任一失败整批回滚 |
| `GET` | `/containers` | 列出全部容器状态 |
| `GET` | `/containers/{container_id}` | 查询某容器当前体积与来源占比 |
| `GET` | `/health` | 健康检查 |

> **精度提示**：体积字段建议用 JSON **字符串**传递（如 `"10.5"` 而非 `10.5`），避免 JSON 浮点在传输中损失精度。响应中的体积与占比均为十进制字符串。

### 1. 登记初始样品

```bash
curl -X POST http://localhost:8000/samples \
  -H 'Content-Type: application/json' \
  -d '{"container_id": "tube-A", "volume": "10.0", "sample_id": "blood-001"}'
```

`sample_id` 可省略，缺省与 `container_id` 同名。重复登记同一容器返回 `409`。

### 2. 批量提交转移

```bash
curl -X POST http://localhost:8000/transfers \
  -H 'Content-Type: application/json' \
  -d '{
    "transfers": [
      {"source": "tube-A", "destination": "tube-B", "volume": "2.5"},
      {"source": "tube-B", "destination": "tube-C", "volume": "1.0"}
    ]
  }'
```

- 转移按顺序**依次发生**：后一条可以使用前一条刚注入的液体。
- 每次转移按源容器**当时**的混合比例，把其全部祖先来源等比带入目标容器。
- 任一转移失败（`insufficient_volume` 源液不足 / `unknown_container` 未知容器 / `self_transfer` 自转移），返回 `400` 且**整批回滚**，此前已执行的转移一并撤销：

```json
{
  "detail": {
    "batch_rejected": true,
    "failed_index": 1,
    "reason": "insufficient_volume",
    "message": "容器 'tube-A' 余量 7.5，不足以转移 9"
  }
}
```

### 3. 查询容器体积与来源占比

```bash
curl http://localhost:8000/containers/tube-C
```

```json
{
  "container_id": "tube-C",
  "volume": "1.0",
  "composition": [
    {"sample_id": "blood-001", "fraction": "0.2", "percentage": "20"},
    {"sample_id": "buffer",    "fraction": "0.8", "percentage": "80"}
  ]
}
```

`composition` 即该容器的谱系：每一项是一个祖先来源（最初登记的样品）及其占比，所有占比之和恒等于 1。

## 本地开发与测试

```bash
pip install -r requirements-dev.txt
pytest                 # 运行测试（11 个用例，覆盖比例传播/精度/整批回滚等）
uvicorn app.main:app --reload   # 本地起服务，默认使用 ./data/lineage.db
```

## 实现说明

- **谱系模型**：`containers` 表存当前体积；`compositions` 表存（容器, 祖先样品, 占比）三元组。转移时源容器混合均匀、占比不变，目标容器按「旧量 × 旧占比 + 移入量 × 源占比」重新归一化，并把舍入残差并入最大来源，保证占比之和精确为 1。
- **原子性**：一批转移在同一个数据库事务中顺序执行，任何一条违反规则即 `rollback`，状态不变。
- **Decimal 存储**：SQLite 的 REAL 会丢精度，因此体积与占比以 `Decimal` 字符串形式落库，计算时还原为 `Decimal`（精度上下文 50 位有效数字）。
- **建库**：应用启动钩子（lifespan）自动执行 `create_all`，无需手动迁移。

## 项目结构

```
├── app/
│   ├── main.py        # FastAPI 应用与路由
│   ├── services.py    # 谱系核心逻辑（Decimal 计算、批量转移、整批回滚）
│   ├── models.py      # SQLAlchemy 模型（containers / compositions）
│   ├── schemas.py     # Pydantic 请求/响应模型
│   └── database.py    # 引擎与会话（DATABASE_URL 配置）
├── tests/test_api.py  # 集成测试
├── Dockerfile
├── docker-compose.yml
├── entrypoint.sh      # 容器启动：确保依赖 → 启动服务（应用启动时建库）
└── requirements.txt
```

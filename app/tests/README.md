# 测试使用指南

## 快速开始

### 1. 启动服务器

```bash
# 开发模式（热重载）
uvicorn app.main:app --reload --port 8000

# 生产模式
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

服务地址：
- API 文档: http://localhost:8000/docs
- 健康检查: http://localhost:8000/health

### 2. 运行测试

```bash
# 基础测试（无需启动服务器）
python app/tests/test_quick.py

# 完整测试（需先启动服务器）
python app/tests/test_quick.py --full
```

### 3. 处理文档

```bash
# 使用测试数据
python app/tests/test_client.py app/tests/data/海洋学院-SR113026-海洋油气地质学.pdf

# 自定义文档
python app/tests/test_client.py /path/to/your/document.pdf

# 查询任务状态
python app/tests/test_client.py --status chatcmpl-xxxxx
```

## 目录结构

```
app/tests/
├── test_client.py      # 客户端测试工具
├── test_quick.py       # 快速测试脚本
├── data/               # 测试数据
│   ├── 海洋学院-SR113026-海洋油气地质学.pdf
│   └── test/
└── results/            # 测试结果（自动创建）
```

## 测试结果

处理结果保存于 `app/tests/results/{task_id}.json`，包含：
- 章节结构与四个模块内容
- 每个知识点的 title、summary、lexicon
- Token 使用统计与处理耗时

## API 端点

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | /health | 健康检查 |
| POST | /api/v1/document/process | 提交文档处理任务 |
| GET | /api/v1/document/status/{task_id} | 查询任务状态 |

## 故障排查

**问题：无法连接到服务器**
```bash
# 检查服务器状态
python app/tests/test_client.py --health

# 启动服务器
uvicorn app.main:app --reload --port 8000
```

**问题：ModuleNotFoundError**
```bash
# 确保在项目根目录
cd /root/workspace/教学大纲四要点核心内容提取工程

# 安装依赖
pip install pydantic-settings
```

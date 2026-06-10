# 入门指南

欢迎使用会话录制与审计系统。本文档将指导您完成系统的设置和使用。

## 目录
1. [前提条件](#前提条件)
2. [快速开始](#快速开始)
3. [核心概念](#核心概念)
4. [录制会话](#录制会话)
5. [审计管线](#审计管线)
6. [仪表板使用](#仪表板使用)
7. [故障排除](#故障排除)

## 前提条件

开始之前，请确保您已具备：

- **Git** 已安装
- **Python 3.8+** 及 pip
- **Docker** 和 **Docker Compose**（用于本地开发）
- 项目仓库访问权限
- 所需的 API 密钥和凭据

## 快速开始

### 1. 克隆仓库
```bash
git clone https://github.com/your-org/session-recorder.git
cd session-recorder
```

### 2. 环境设置
```bash
cp .env.example .env
# 使用您的凭据编辑 .env
```

### 3. 安装依赖
```bash
pip install -r requirements.txt
```

### 4. 启动服务
```bash
docker-compose up -d
```

### 5. 运行录制器
```bash
python -m recorder.cli start --session-id my-first-session
```

## 核心概念

### 录制会话
**录制会话** 表示具有唯一标识符的单个录制实例。会话捕获用户交互、系统事件和元数据。

### 标准管线
**标准管线** 是将原始会话数据转换为具有完整溯源信息的可审计记录的标准处理工作流。

### 审计
**审计** 是指审查和验证会话录制是否符合合规性和质量保证要求的过程。

### 溯源
**溯源** 跟踪数据转换的完整谱系，从原始录制到最终审计报告。

### 监控守护
**监控守护** 是一个监控守护进程，确保系统健康并在异常时发出警报。

### 路线类型
**路线类型** 定义录制会话中用户导航路径的分类。

### 回放
**回放** 允许您以完整保真度查看录制的会话，包括屏幕捕获、用户交互和系统事件。

## 录制会话

### 开始录制
```bash
python -m recorder.cli start \
  --session-id "user-123-session" \
  --output-dir ./sessions \
  --metadata '{"user_id": 123, "environment": "production"}'
```

### 录制配置
创建 `recorder_config.yaml`：
```yaml
session:
  max_duration: 3600  # 秒
  compression: gzip
  encryption: true
  
capture:
  screen: true
  audio: false
  network: true
  system_events: true
  
storage:
  backend: s3
  bucket: session-recordings
  region: us-east-1
```

### 会话元数据
每个会话包括：
- **session_id**：唯一标识符
- **start_time**：ISO 8601 时间戳
- **user_context**：用户信息
- **environment**：生产/预发布/开发环境
- **route_type**：初始导航分类

## 审计管线

### 管线阶段
1. **摄取**：原始会话数据接收
2. **验证**：数据完整性检查
3. **转换**：规范化和丰富化
4. **分析**：模式检测和异常评分
5. **报告**：审计报告生成

### 运行管线
```bash
python -m pipeline.process \
  --session-id "user-123-session" \
  --pipeline canonical \
  --output-format json
```

### 审计报告
审计报告包括：
- 会话完整性评分
- 数据完整性验证
- 异常检测结果
- 溯源链验证
- 合规性检查清单

## 仪表板使用

### 登录
访问仪表板：`http://localhost:8080`

1. 点击**登录**并输入您的凭据
2. 通过 OAuth 2.0 进行身份验证
3. 选择您的组织

### 会话管理
- **查看会话**：浏览所有录制的会话
- **按路线类型筛选**：按导航类型筛选会话
- **搜索**：按 ID 或元数据查找会话
- **导出**：以各种格式下载会话数据
- **回放**：使用完整回放功能查看录制的会话

### 审计界面
- **批准**：标记会话为合规
- **拒绝**：标记会话需要审查
- **评论**：添加审计备注
- **支付待处理**：等待处理的会话

### 监控守护状态
监控系统健康：
- **活动会话**：当前正在录制
- **管线吞吐量**：每小时处理的会话数
- **错误率**：录制失败百分比
- **存储使用情况**：磁盘/S3 使用率

## 故障排除

### 常见问题

#### "无法启动录制器"
```bash
# 检查端口是否可用
netstat -an | grep 8080

# 验证 Docker 是否运行
docker ps
```

#### "会话数据损坏"
```bash
# 运行数据验证
python -m recorder.validate --session-id problematic-session

# 检查存储后端
aws s3 ls s3://session-recordings/
```

#### "仪表板登录失败"
1. 清除 localhost 的浏览器 cookies
2. 验证 `.env` 中的 OAuth 凭据
3. 检查后端服务日志：
```bash
docker-compose logs auth-service
```

### 获取帮助

- **文档**：查看 `/docs` 获取详细指南
- **问题跟踪**：GitHub Issues 用于错误报告
- **Slack 频道**：#session-recorder-support
- **电子邮件**：support@session-recorder.example.com

## 后续步骤

1. **完成您的第一次录制**：尝试快速开始指南
2. **查看审计报告**：了解分析输出
3. **使用回放功能**：查看录制的会话
4. **自定义管线**：根据您的用例进行修改
5. **与您的系统集成**：可用的 API 文档
6. **加入社区**：为项目做出贡献

---

*最后更新：2024-01-15*
*版本：2.1.0*
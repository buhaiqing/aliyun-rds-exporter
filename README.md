# aliyun-rds-exporter

阿里云 RDS (关系型数据库服务) 的 Prometheus Exporter，用于将阿里云 RDS 实例的性能指标、资源使用情况、状态信息等导出为 Prometheus 格式的监控数据。

## 功能特性

- 采集阿里云 RDS 实例的性能指标、资源使用情况和状态信息
- 支持 MySQL、SQLServer、PostgreSQL 等多种数据库引擎
- 提供标准的 Prometheus 指标接口
- 支持配置化采集特定指标
- 支持对特定实例添加自定义标签
- 支持资源组过滤
- 使用缓存机制减少 API 调用频率
- 支持并发采集提高效率

## 目录结构

```
aliyun-rds-exporter/
├── main.py                      # 主入口文件,启动 HTTP 服务
├── tools.py                     # 工具函数,解析命令行参数和配置文件
├── requirements.txt              # Python 依赖包列表
├── Dockerfile                   # Docker 镜像构建文件
├── config/
│   ├── config.yaml.example       # 配置文件示例
│   └── config.yaml             # 实际配置文件 (需自行创建)
├── module/
│   ├── __init__.py
│   └── collector.py            # 核心采集器,实现 Prometheus Collector 接口
├── kubernetes/                  # Kubernetes 部署文件
│   ├── aliyun-rds-exporter-configmap.yaml
│   ├── aliyun-rds-exporter-deployment.yaml
│   ├── aliyun-rds-exporter-service.yaml
│   ├── aliyun-rds-exporter-servicemonitor.yaml
│   └── aliyun-rds-exporter-prometheusrules.yaml
```

## 安装与部署

### Docker 方式

```bash
# 构建 Docker 镜像
docker build -t aliyun_rds_exporter:0.1 .

# 复制配置文件
cp config/config.yaml.example config/config.yaml
# 修改 config/config.yaml 中的阿里云凭证和其他配置

# 运行 exporter (Docker)
docker run -d --restart=on-failure:5 -p 5234:5234 \
  -v config/config.yaml:/opt/aliyun-rds-exporter/config/config.yaml \
  aliyun_rds_exporter:0.1
```

### 本地运行

```bash
# 安装依赖
pip install -r requirements.txt

# 运行 exporter
python main.py

# 调试模式运行
python main.py -d

# 指定配置文件
python main.py -c /path/to/config.yaml
```

### Kubernetes 部署

```bash
# 部署到 Kubernetes 集群
kubectl apply -f kubernetes/

# 查看部署状态
kubectl get pods -l app=aliyun-rds-exporter
```

## 配置说明

配置文件 [config.yaml.example](config/config.yaml.example) 中包含以下配置项：

```yaml
# 阿里云认证信息
aliyun:
  access_key_id: ""
  access_key_secret: ""
  region_id: "cn-beijing"
  resource_group_id: ""  # 可选，指定资源组ID

# 服务器配置
server:
  host: "0.0.0.0"
  port: 5234

# 采集器配置
collector:
  performance_list:  # 性能指标列表
    - "MySQL_NetworkTraffic"
    - "MySQL_QPSTPS"
    - "MySQL_Sessions"
    # 更多指标...
  additional_labels:  # 为特定实例添加额外标签
    i-xxxxx:
      project: "project-name"
      environment: "production"
  included_instances: []  # 指定要采集的实例ID列表，留空则采集全部
  excluded_instances: []  # 指定不采集的实例ID列表
```

## 指标说明

Exporter 会生成以下几类 Prometheus 指标：

### 性能指标

- `aliyun_rds_performance_*` - RDS 实例性能指标
  - 示例: `aliyun_rds_performance_MySQL_NetworkTraffic_recv_k`
  - 类型: Gauge
  - 描述: MySQL 网络接收流量指标
  - 标签: instance_id, region_id 等

### 资源使用指标

- `aliyun_rds_resource_usage_*` - RDS 实例资源使用情况
  - 示例: `aliyun_rds_resource_usage_LogSize`
  - 类型: Gauge
  - 描述: RDS 实例日志大小
  - 标签: instance_id, region_id 等

### 实例状态指标

- `aliyun_rds_status` - RDS 实例状态信息
  - 类型: Gauge
  - 描述: RDS 实例状态指标
  - 标签: CreateTime, Engine, RegionId, DBInstanceType 等

### 实例规格指标

- `aliyun_rds_spec_*` - RDS 实例规格信息
  - 示例: `aliyun_rds_spec_MaxConnections`
  - 类型: Gauge
  - 描述: RDS 实例最大连接数
  - 标签: instance_id, region_id 等

## 查询指标

启动后可以通过以下地址访问指标:

```bash
curl http://localhost:5234/metrics
```

## 阿里云配置要求

要使用此 Exporter，您需要:

1. 创建阿里云 RAM 用户并获取 Access Key ID 和 Access Key Secret
2. 为该用户授予 `AliyunRDSReadOnlyAccess` 权限
3. 在配置文件中填入相应的认证信息

## 开发

本项目采用分层架构，主要分为三个模块：

1. **配置层** (`tools.py` + `config/config.yaml`)
   - 解析命令行参数和配置文件
   - 封装配置项

2. **采集层** (`module/collector.py`)
   - 核心采集器，实现 Prometheus Collector 接口
   - 使用阿里云 SDK 调用 RDS API
   - 使用缓存和并发机制提升性能

3. **服务层** (`main.py`)
   - 基于 prometheus_client 创建 WSGI 应用
   - 提供 HTTP 服务

## 相关文档

- **RDS API 概览**: https://help.aliyun.com/document_detail/26226.html
- **各引擎性能指标参数**: https://help.aliyun.com/document_detail/26316.html
- **查询实例列表 API**: https://help.aliyun.com/zh/rds/api-query-instances
- **查询性能数据 API**: https://help.aliyun.com/zh/rds/api-query-performance-metrics
- **查询资源使用 API**: https://help.aliyun.com/zh/rds/api-query-storage-usage
- **查询实例详情 API**: https://help.aliyun.com/zh/rds/api-query-instance-details

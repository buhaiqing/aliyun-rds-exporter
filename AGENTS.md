# AGENTS.md This file provides guidance to agents when working with code in this repository.

## 项目概述
这是一个阿里云 RDS (关系型数据库服务) 的 Prometheus Exporter,用于将阿里云 RDS 实例的性能指标、资源使用情况、状态信息等导出为 Prometheus 格式的监控数据。

## 项目目录结构
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

## 常用命令

### 构建和运行
```bash
# 构建 Docker 镜像
docker build -t aliyun_rds_exporter:0.1 .

# 复制配置文件
cp config/config.yaml.example config/config.yaml

# 运行 exporter (Docker)
docker run -d --restart=on-failure:5 -p 5234:5234 \
  -v config/config.yaml:/opt/aliyun-rds-exporter/config/config.yaml \
  my_aliyun_rds_exporter:0.1

# 查询指标
curl http://localhost:5234/metrics
```

### 本地开发和调试
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

## 架构说明

### 核心组件
项目采用分层架构,主要分为三个模块:

1. **配置层** (`tools.py` + `config/config.yaml`)
   - 解析命令行参数 (`get_args()`): 支持 `-c/--config` 指定配置文件路径,`-d/--debug` 开启调试模式
   - 解析 YAML 配置文件 (`get_file_opts()`): 包含阿里云凭证、服务器配置、性能指标列表等
   - `CollectorConfig`: 封装所有配置项,包括凭证、性能指标列表、附加标签、资源组 ID 等

2. **采集层** (`module/collector.py`)
   - `AliyunRDSCollector`: 核心采集器,继承自 `prometheus_client.core.Collector`
   - 使用阿里云 Python SDK (`aliyun-python-sdk-rds`) 调用 RDS API
   - 采用 `@cached` 装饰器实现 TTL 缓存,减少 API 调用频率:
     - `query_rds_instance_list()`: TTL 300 秒
     - `query_rds_performance_data_list()`: TTL 50 秒
     - `query_rds_resource_usage_list()`: TTL 60 秒
   - 使用 `concurrent.futures.ThreadPoolExecutor` 实现并发 API 调用,提升采集效率

3. **服务层** (`main.py`)
   - 基于 `prometheus_client` 创建 WSGI 应用
   - 使用 `werkzeug.middleware.dispatcher.DispatcherMiddleware` 作为中间件
   - 通过 `wsgiref.simple_server` 提供 HTTP 服务,默认监听 `0.0.0.0:5234`

### 指标采集流程
1. **实例列表采集**: 调用 `DescribeDBInstancesRequest` API 获取 RDS 实例列表,支持分页和资源组过滤
2. **性能指标采集**: 针对每个实例,根据配置的 `performance_list` 调用 `DescribeDBInstancePerformanceRequest`,采集最近 3 分钟的性能数据
3. **资源使用采集**: 调用 `DescribeResourceUsageRequest` 获取磁盘空间、日志大小等资源使用情况
4. **实例规格采集**: 调用 `DescribeDBInstanceAttributeRequest` 获取实例规格信息 (CPU、内存、最大连接数、最大 IOPS)
5. **实例状态采集**: 从实例列表中提取状态信息 (运行状态、实例类型、引擎版本等)

### 生成的 Prometheus 指标类型
- **性能指标**: `aliyun_rds_performance_*` (如 `MySQL_NetworkTraffic_recv_k`, `MySQL_QPSTPS_QPS`)
- **资源使用指标**: `aliyun_rds_resource_usage_*` (如 `LogSize`, `DataSize`, `DiskUsed`)
- **实例状态指标**: `aliyun_rds_status` (包含 CreateTime, Engine, RegionId 等标签)
- **实例规格指标**: `aliyun_rds_spec` (如 MaxConnections, MaxIOPS, DBInstanceMemory)

### 配置要点
- **凭证配置**: 必须配置 `access_key_id`, `access_key_secret`, `region_id`
- **资源组**: 可选配置 `resource_groupId`,用于限定访问特定资源组下的实例
- **性能指标**: 通过 `performance_list` 配置需要采集的性能指标,支持 MySQL、SQLServer、PostgreSQL 三种引擎
- **附加标签**: 可通过 `additional_labels` 为指定实例添加自定义标签 (如 project、product、profile)
- **实例过滤**: 可通过 `included_instances` 指定只采集特定实例

### 阿里云官方文档
- **RDS API 概览**: https://help.aliyun.com/document_detail/26226.html
- **各引擎性能指标参数**: https://help.aliyun.com/document_detail/26316.html
- **查询实例列表 API**: https://help.aliyun.com/zh/rds/api-query-instances
- **查询性能数据 API**: https://help.aliyun.com/zh/rds/api-query-performance-metrics
- **查询资源使用 API**: https://help.aliyun.com/zh/rds/api-query-storage-usage
- **查询实例详情 API**: https://help.aliyun.com/zh/rds/api-query-instance-details

### 用户手册 

config.yaml的配置有清晰完整的说明，要求通俗易懂。
运行采集的prometheus指标有清晰完整的说明与示例,指标说明中一定要包含指标类型的描述，要求通俗易懂。
内容存档到README.md
###  Code Quality Assurance (QA)


- **代码精简性**: 保持逻辑不变前提下，代码最精简、清晰、易于阅读
- **性能优化**: 极致优化代码执行性能，减少不必要的计算和内存消耗
- **测试覆盖**: 确保关键业务逻辑有完整的单元测试覆盖
- **注释一致性**: 代码注释与逻辑保持一致，避免过时注释
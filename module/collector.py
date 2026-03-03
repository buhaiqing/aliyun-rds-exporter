#!/usr/bin/env python3
# -*- coding:utf-8 -*-

import json
import datetime
import sys
import logging
import asyncio
from concurrent import futures
from cachetools import cached, TTLCache, cachedmethod
from aliyunsdkcore.client import AcsClient
from aliyunsdkrds.request.v20140815.DescribeDBInstancesRequest import DescribeDBInstancesRequest
from aliyunsdkrds.request.v20140815.DescribeDBInstancePerformanceRequest import DescribeDBInstancePerformanceRequest
from aliyunsdkrds.request.v20140815.DescribeResourceUsageRequest import DescribeResourceUsageRequest
from aliyunsdkrds.request.v20140815.DescribeDBInstanceAttributeRequest import DescribeDBInstanceAttributeRequest
from prometheus_client.core import Summary, GaugeMetricFamily, InfoMetricFamily
from prometheus_client import Counter, Info

# 这里的api_request是用来记录阿里云API调用的延迟
api_request_summry = Summary(
    'aliyun_api_request_latency_seconds',
    'CloudMonitor request latency',
    ['api']
)
api_request_failed_summry = Summary(
    'aliyun_api_failed_request_latency_seconds',
    'CloudMonitor failed request latency',
    ['api']
)
# 记录阿里云API调用次数
api_request_count = Counter(
    'aliyun_api_request_counter',
    'Aliyun API request counter',
)


def raw_jsonpath_query(data, expression):
    import jsonpath
    return jsonpath.jsonpath(data, expression)


def jsonpath_query(data, expression):
    """
     支持JSONPath表达式查询JSON数据
     links:
     - https://pypi.org/project/jsonpath/
     - http://goessner.net/articles/JsonPath/
    :param expression:
    :param data:
    :return:
    """
    matches = raw_jsonpath_query(data, expression)
    if matches and len(matches) == 1:
        return matches[0]
    else:
        return matches


class CollectorConfig(object):
    def __init__(self, file_opts, command_args, page_size=20, rate_limit=10, ):
        self.command_args = command_args
        self.rate_limit = rate_limit
        self.page_size = page_size
        self.additional_labels = file_opts.get('additional_labels') or []
        self.included_instances = file_opts.get('included_instances') or []
        self.server = file_opts['server']
        self.credential = file_opts['credential']
        self.resource_groupId = file_opts.get("resource_groupId","")
        self.performance_list = file_opts['performance_list']
        if (
                (self.credential['access_key_id'] is None)
                or
                (self.credential['access_key_secret'] is None)
                or
                (self.credential['region_id'] is None)
        ):
            raise Exception('Credential in config file not fully configured!')


class AliyunRDSCollector(object):
    def __init__(self, config):
        self.credential = config.credential
        self.resource_groupId = config.resource_groupId
        self.rate_limit = config.rate_limit
        self.page_size = config.page_size
        self.client = AcsClient(
            ak=self.credential['access_key_id'],
            secret=self.credential['access_key_secret'],
            region_id=self.credential['region_id'],
        )
        self.config = config

    def get_additional_labels(self, instance_id: str):
        if not self.config.additional_labels:
            return {}
        for inst in self.config.additional_labels:
            if inst['id'] == instance_id:
                return inst['labels']
        return {}

    @cached(cache=TTLCache(maxsize=4096, ttl=300))
    def query_rds_instance_list(self):
        # query_rds_instance_list用于请求RDS数据库实例和返回数据库实例状态列表
        page_num = 1
        request = DescribeDBInstancesRequest()
        if self.resource_groupId:
            request.set_ResourceGroupId(self.resource_groupId)
        request.set_PageSize(self.page_size)
        request.set_accept_format('json')
        rds_instance_list = []
        now = datetime.datetime.now().timestamp()
        while True:
            try:
                request.set_PageNumber(page_num)
                response = json.loads(self.client.do_action_with_exception(request).decode('utf-8'))
                api_request_summry.labels(api='DescribeDBInstancesRequest').observe(
                    amount=(datetime.datetime.now().timestamp() - now)
                )
                api_request_count.inc()
            except Exception as e:
                logging.error('Error request Aliyun api', exc_info=e)
                api_request_failed_summry.labels(api='DescribeDBInstancesRequest').observe(
                    amount=(datetime.datetime.now().timestamp() - now)
                )
                api_request_count.inc()
                return []
            if response['PageRecordCount'] == 0:
                break
            DBInstance_list = response['Items']['DBInstance']
            rds_instance_list.extend(DBInstance_list)
            page_num += 1
        if self.config.included_instances:
            rds_instance_list = [
                i for i in rds_instance_list if i['DBInstanceId'] in self.config.included_instances
            ]
        logging.debug("size of rds_instance_list = {}".format(sys.getsizeof(rds_instance_list)))
        return rds_instance_list

    @cached(cache=TTLCache(maxsize=4096, ttl=50))
    def query_rds_performance_data_list(self):
        """
        批量查询RDS实例性能数据。
        利用阿里云API的Key参数支持批量查询特性（每批次最多30个指标），
        大幅减少API调用次数，提升采集性能。
        """
        rds_instance_list = self.query_rds_instance_list()
        instance_count = len(rds_instance_list)
        logging.info(f"[Performance] 开始采集性能数据，共 {instance_count} 个实例")

        now = datetime.datetime.utcnow()
        starttime = (now - datetime.timedelta(minutes=3)).strftime('%Y-%m-%dT%H:%MZ')
        endtime = now.strftime('%Y-%m-%dT%H:%MZ')
        performance_lists = self.config.performance_list

        # 每批次最多30个指标（阿里云API限制）
        BATCH_SIZE = 30
        request_task_list = []

        for instance in rds_instance_list:
            DBInstanceId = instance['DBInstanceId']
            Engine = instance['Engine']
            rds_performance_list = performance_lists.get(Engine, [])

            if not rds_performance_list:
                logging.warning(f"[Performance] 实例 {DBInstanceId} 引擎类型 {Engine} 没有配置性能指标")
                continue

            # 将指标列表按 BATCH_SIZE 分批
            for batch_start in range(0, len(rds_performance_list), BATCH_SIZE):
                batch_keys = rds_performance_list[batch_start:batch_start + BATCH_SIZE]
                batch_keys_str = ','.join(batch_keys)

                request = DescribeDBInstancePerformanceRequest()
                request.set_DBInstanceId(DBInstanceId)
                request.set_accept_format('json')
                request.set_StartTime(starttime)
                request.set_EndTime(endtime)
                request.set_Key(batch_keys_str)

                request_task_list.append({
                    'request': request,
                    'instance_id': DBInstanceId,
                    'engine': Engine,
                    'batch_keys': batch_keys,
                    'batch_index': batch_start // BATCH_SIZE
                })

        total_batches = len(request_task_list)
        logging.info(f"[Performance] 共生成 {total_batches} 个批量查询请求（每批最多{BATCH_SIZE}个指标）")

        if not request_task_list:
            logging.info("[Performance] 没有需要查询的性能数据")
            return []

        # 批量执行API请求
        rds_performance_data_list = []
        with futures.ThreadPoolExecutor(50) as executor:
            future_to_task = {
                executor.submit(
                    self._fetch_performance_batch,
                    task['request'],
                    task['instance_id'],
                    task['engine'],
                    task['batch_keys'],
                    task['batch_index']
                ): task
                for task in request_task_list
            }

            for future in futures.as_completed(future_to_task):
                task = future_to_task[future]
                try:
                    result = future.result()
                    if result:
                        rds_performance_data_list.append(result)
                except Exception as e:
                    logging.error(
                        f"[Performance] 实例 {task['instance_id']} 第 {task['batch_index'] + 1} 批指标查询失败: {e}"
                    )

        logging.info(f"[Performance] 性能数据采集完成，成功获取 {len(rds_performance_data_list)} 批数据")
        return rds_performance_data_list

    def _fetch_performance_batch(self, request, instance_id, engine, batch_keys, batch_index):
        """
        执行单个批量性能数据查询请求，包含详细日志记录。
        """
        start_time = datetime.datetime.now()
        keys_str = ','.join(batch_keys)
        logging.debug(f"[Performance] 开始查询实例 {instance_id} 第 {batch_index + 1} 批指标: {keys_str}")

        try:
            response = self.client.do_action_with_exception(request)
            elapsed = (datetime.datetime.now() - start_time).total_seconds()
            api_request_summry.labels(api='DescribeDBInstancePerformanceRequest').observe(amount=elapsed)
            api_request_count.inc()

            logging.debug(
                f"[Performance] 实例 {instance_id} 第 {batch_index + 1} 批指标查询成功，"
                f"耗时 {elapsed:.3f}s，指标数: {len(batch_keys)}"
            )
            return response

        except Exception as e:
            elapsed = (datetime.datetime.now() - start_time).total_seconds()
            api_request_failed_summry.labels(api='DescribeDBInstancePerformanceRequest').observe(amount=elapsed)
            api_request_count.inc()
            logging.error(
                f"[Performance] 实例 {instance_id} 第 {batch_index + 1} 批指标查询失败，"
                f"耗时 {elapsed:.3f}s，指标: {keys_str}，错误: {e}"
            )
            return None

    @cached(cache=TTLCache(maxsize=1024, ttl=60))
    def query_rds_resource_usage_list(self):
        rds_instance_list = self.query_rds_instance_list()
        request_task_list = []
        for i in range(len(rds_instance_list)):
            request = DescribeResourceUsageRequest()
            DBInstanceId = rds_instance_list[i]['DBInstanceId']
            request.set_DBInstanceId(DBInstanceId=DBInstanceId)
            request_task_list.append(request)
        with futures.ThreadPoolExecutor(50) as executor:
            response = executor.map(self.aliyun_client_do_action, request_task_list)
        rds_resource_usage_list = list(response)
        return rds_resource_usage_list

    def aliyun_client_do_action(self, request):
        now = datetime.datetime.now().timestamp()
        try:
            response = self.client.do_action_with_exception(request)
            api_request_summry.labels(api='DescribeDBInstancePerformanceRequest').observe(
                amount=(datetime.datetime.now().timestamp() - now)
            )
            api_request_count.inc()
            logging.debug("aliyun_client_do_action_response = {}".format(response))
            return response
        except Exception as e:
            logging.error('Error request Aliyun api', exc_info=e)
            api_request_failed_summry.labels(api='DescribeDBInstancePerformanceRequest').observe(
                amount=(datetime.datetime.now().timestamp() - now)
            )
            api_request_count.inc()
            return []

    def generate_rds_performance_metrics(self):
        """
        生成RDS性能指标Prometheus指标。
        处理批量查询返回的数据，遍历每个响应中的所有PerformanceKey。
        """
        now = datetime.datetime.now()
        rds_performance_data_list = self.query_rds_performance_data_list()
        total_metrics = 0
        logging.info(f"[Metrics] 开始生成性能指标，共 {len(rds_performance_data_list)} 批响应数据")

        for i, response_data in enumerate(rds_performance_data_list):
            if not response_data or len(response_data) == 0:
                logging.warning(f"[Metrics] rds_performance_data_list[{i}] 为空")
                continue

            try:
                rds_performance_data = json.loads(response_data.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                logging.error(f"[Metrics] 解析第 {i} 批响应数据失败: {e}")
                continue

            DBInstanceId = rds_performance_data.get("DBInstanceId", "unknown")
            performance_keys = rds_performance_data.get('PerformanceKeys', {}).get('PerformanceKey', [])

            if not performance_keys:
                logging.warning(f"[Metrics] 实例 {DBInstanceId} 没有返回性能指标数据")
                continue

            logging.debug(f"[Metrics] 实例 {DBInstanceId} 返回 {len(performance_keys)} 个指标")
            additional_labels = self.get_additional_labels(DBInstanceId)

            # 遍历该响应中的所有指标（批量查询会返回多个指标）
            for perf_key in performance_keys:
                Key = perf_key.get("Key", "")
                Unit = perf_key.get("Unit", "")
                values_list = perf_key.get("Values", {}).get("PerformanceValue", [])

                if not values_list:
                    logging.warning(f"[Metrics] 实例 {DBInstanceId} 指标 {Key} 没有数值数据")
                    continue

                # 获取最新时间点的数据
                latest_value = values_list[-1].get("Value", "")
                if not latest_value:
                    logging.warning(f"[Metrics] 实例 {DBInstanceId} 指标 {Key} 最新值为空")
                    continue

                Value = latest_value.split("&")
                ValueFormat = perf_key.get("ValueFormat", "").split("&")

                if len(Value) != len(ValueFormat):
                    logging.warning(
                        f"[Metrics] 实例 {DBInstanceId} 指标 {Key} 值格式不匹配: "
                        f"值数量 {len(Value)} vs 格式数量 {len(ValueFormat)}"
                    )
                    continue

                for k, v in zip(ValueFormat, Value):
                    name = f"aliyun_rds_performance_{Key}_{k}".replace('-', '_')
                    try:
                        float_value = float(v)
                    except (ValueError, TypeError):
                        logging.debug(f"[Metrics] 实例 {DBInstanceId} 指标 {name} 值 '{v}' 无法转换为数字，跳过")
                        continue

                    logging.debug(f"[Metrics] {name} = {float_value} (实例: {DBInstanceId})")
                    gauge = GaugeMetricFamily(
                        name=name,
                        documentation=f'RDS performance metric: {Key}',
                        labels=["instanceId", "Unit"] + list(additional_labels.keys())
                    )
                    gauge.add_metric(
                        labels=[DBInstanceId, Unit] + list(additional_labels.values()),
                        value=float_value,
                    )
                    yield gauge
                    total_metrics += 1

        elapsed = (datetime.datetime.now() - now).total_seconds()
        logging.info(f"[Metrics] 性能指标生成完成，共生成 {total_metrics} 个指标，耗时 {elapsed:.3f}s")

    def query_rds_specs_list(self):
        rds_instance_list = self.query_rds_instance_list()
        request_task_list = []
        for i in range(len(rds_instance_list)):
            rds_status = rds_instance_list[i]
            rds_id = rds_status['DBInstanceId']
            if len(rds_status) == 0:
                logging.warning("rds_status == {}".format(rds_status))
                continue
            req = DescribeDBInstanceAttributeRequest()
            req.set_DBInstanceId(rds_id)
            request_task_list.append(req)
        with futures.ThreadPoolExecutor(50) as executor:
            response = executor.map(self.aliyun_client_do_action, request_task_list)
        data_list = list(response)
        data_list = [jsonpath_query(json.loads(i), '$..Items.DBInstanceAttribute[*]') for i in data_list]
        return data_list

    def generate_rds_specs(self):
        rds_instance_list = self.query_rds_specs_list()
        # DBInstanceId,RegionId - MaxConnections,MaxIOPS,DBInstanceMemory,DBInstanceCPU
        for inst in rds_instance_list:
            DBInstanceId = inst["DBInstanceId"]
            RegionId = inst["RegionId"]
            MaxConnections = inst.get('MaxConnections', None)
            MaxIOPS = inst.get("MaxIOPS", None)
            DBInstanceMemory = inst.get('DBInstanceMemory', None)
            DBInstanceCPU = inst.get('DBInstanceCPU', None)

            additional_labels = self.get_additional_labels(DBInstanceId)

            keys = ["instanceId", "MetricName"]
            if MaxConnections:
                metric_name = "MaxConnections"
                gauge = GaugeMetricFamily(
                    name="aliyun_rds_spec",
                    documentation='',
                    labels=keys + list(additional_labels.keys())
                )
                gauge.add_metric(
                    labels=[DBInstanceId, metric_name] + list(additional_labels.values()),
                    value=MaxConnections,
                )
                yield gauge
            if MaxIOPS:
                metric_name = "MaxIOPS"
                gauge = GaugeMetricFamily(
                    name="aliyun_rds_spec",
                    documentation='',
                    labels=keys + list(additional_labels.keys())
                )
                gauge.add_metric(
                    labels=[DBInstanceId, metric_name] + list(additional_labels.values()),
                    value=MaxIOPS,
                )
                yield gauge
            if DBInstanceMemory:
                metric_name = "DBInstanceMemory"
                gauge = GaugeMetricFamily(
                    name="aliyun_rds_spec",
                    documentation='',
                    labels=keys + list(additional_labels.keys())
                )
                gauge.add_metric(
                    labels=[DBInstanceId, metric_name] + list(additional_labels.values()),
                    value=DBInstanceMemory,
                )
                yield gauge
            if DBInstanceCPU:
                metric_name = "DBInstanceCPU"
                gauge = GaugeMetricFamily(
                    name="aliyun_rds_spec",
                    documentation='',
                    labels=keys + list(additional_labels.keys())
                )
                gauge.add_metric(
                    labels=[DBInstanceId, metric_name] + list(additional_labels.values()),
                    value=DBInstanceCPU,
                )
                yield gauge

    def generate_rds_status_metrics(self):
        rds_instance_list = self.query_rds_instance_list()
        for i in range(len(rds_instance_list)):
            rds_status = rds_instance_list[i]
            if len(rds_status) == 0:
                logging.warning("rds_status == {}".format(rds_status))
                continue
            # logging.info("rds_status = {}".format(rds_status))
            rds_status_keys = [
                "CreateTime",
                "DBInstanceDescription",
                "instanceId",
                "DBInstanceStatus",
                "DBInstanceType",
                "Engine",
                "EngineVersion",
                "ExpireTime",
                "LockMode",
                "PayType",
                "RegionId",
            ]
            # if rds_status["DBInstanceStatus"] != "Running":
            #     continue
            gauge = GaugeMetricFamily(
                name="aliyun_rds_status",
                documentation='',
                labels=rds_status_keys,
            )
            gauge.add_metric(
                [
                    rds_status["CreateTime"],
                    rds_status["DBInstanceDescription"],
                    rds_status["DBInstanceId"],
                    rds_status["DBInstanceStatus"],
                    rds_status["DBInstanceType"],
                    rds_status["Engine"],
                    rds_status["EngineVersion"],
                    rds_status["ExpireTime"],
                    rds_status["LockMode"],
                    rds_status["PayType"],
                    rds_status["RegionId"],
                ],
                value=1
            )
            yield gauge

    def generator_rds_resource_usage_metrics(self):
        now = datetime.datetime.now()
        rds_resource_usage_list = self.query_rds_resource_usage_list()
        logging.debug("query_rds_resource_usage_list used time = {}".format(datetime.datetime.now() - now))
        for i in range(len(rds_resource_usage_list)):
            logging.debug("rds_resource_usage = {}".format(rds_resource_usage_list[i]))
            rds_resource_usage = json.loads(rds_resource_usage_list[i].decode("utf-8"))
            if len(rds_resource_usage.items()) == 1 or len(rds_resource_usage) == 0:
                logging.debug("rds_resource_usage = {}".format(rds_resource_usage))
                continue
            DBInstanceId = rds_resource_usage["DBInstanceId"]
            Engine = rds_resource_usage["Engine"]
            additional_labels = self.get_additional_labels(DBInstanceId)
            for k, v in rds_resource_usage.items():
                if k == 'Engine' or k == 'RequestId' or k == 'DBInstanceId':
                    continue
                name = "aliyun_rds_resource_usage_{}".format(k)
                gauge = GaugeMetricFamily(
                    name=name,
                    documentation='',
                    labels=["instanceId", "Engine"] + list(additional_labels.keys())
                )
                gauge.add_metric(
                    [DBInstanceId, Engine] + list(additional_labels.values()),
                    value=v,
                )
                yield gauge

    def collect(self):
        yield from self.generate_rds_performance_metrics()
        yield from self.generator_rds_resource_usage_metrics()
        yield from self.generate_rds_status_metrics()
        yield from self.generate_rds_specs()

FROM harbor.qianfan123.com/base/python:3.10.7-slim-buster-hd
LABEL PROJECT="aliyun-rds-exporter" \
      VERSION="0.1.0"             \
      AUTHOR="buhaiqing"              \
      COMPANY="Shanghai HEADING Information Engineering Co., Ltd."
MAINTAINER buhaiqing "buhaiqing@hd123.com"

WORKDIR /opt/aliyun-rds-exporter
ADD . /opt/aliyun-rds-exporter
RUN \
    pip config set global.index-url https://mirrors.163.com/pypi/simple && \
    pip install -r requirements.txt && \
    chmod a+rx main.py
EXPOSE 5234

CMD ["python", "main.py"]
"""接口出入参模型：列表分页、动作结果与各模块的明细结构。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20


class ActionResult(BaseModel):
    ok: bool
    message: str
    entry: dict[str, Any] | None = None


class EntryPayload(BaseModel):
    """登记或修改一条业务记录时提交的字段集合。"""

    values: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = None


class ActionPayload(BaseModel):
    """状态动作使用独立契约，避免与登记的 values 载荷混用。"""

    action: str
    remark: str | None = None


class DeliveryActionPayload(BaseModel):
    """分段交付动作：确认/中断针对具体分段，恢复针对整个交付版本。"""

    action: str
    segment_seq: int | None = None
    remark: str | None = None


class SourceUserUpdate(BaseModel):
    """来源成员的更正字段；只允许改消费额等可编辑业务字段。"""

    ltv: float | None = None
    region: str | None = None
    channel: str | None = None
    tag: str | None = None
    last_order_date: str | None = None



class MetricDefEntry(BaseModel):
    """业务指标明细结构。"""

    field_0: str | None = None  # 指标编号
    field_1: str | None = None  # 指标名称
    field_2: str | None = None  # 计算公式
    field_3: str | None = None  # 聚合粒度
    field_4: str | None = None  # 时间窗口
    field_5: str | None = None  # 口径版本
    field_6: str | None = None  # 数据来源
    field_7: str | None = None  # 精度位数

class DimRegistryEntry(BaseModel):
    """分析维度明细结构。"""

    field_0: str | None = None  # 维度编号
    field_1: str | None = None  # 维度名称
    field_2: str | None = None  # 业务键
    field_3: str | None = None  # 层级结构
    field_4: str | None = None  # 历史策略
    field_5: str | None = None  # 数据来源表
    field_6: str | None = None  # 刷新周期
    field_7: str | None = None  # 生效标记

class AggTaskEntry(BaseModel):
    """聚合计算明细结构。"""

    field_0: str | None = None  # 任务编号
    field_1: str | None = None  # 目标指标
    field_2: str | None = None  # 聚合维度
    field_3: str | None = None  # 计算周期
    field_4: str | None = None  # 依赖上游
    field_5: str | None = None  # 并行度
    field_6: str | None = None  # 超时分钟
    field_7: str | None = None  # 重试上限

class CubeBuilderEntry(BaseModel):
    """数据立方体明细结构。"""

    field_0: str | None = None  # 立方体编号
    field_1: str | None = None  # 事实来源
    field_2: str | None = None  # 维度列表
    field_3: str | None = None  # 度量列表
    field_4: str | None = None  # 预计算粒度
    field_5: str | None = None  # 存储策略
    field_6: str | None = None  # 数据量级
    field_7: str | None = None  # 最后刷新

class CohortRuleEntry(BaseModel):
    """用户分群明细结构。"""

    field_0: str | None = None  # 分群编号
    field_1: str | None = None  # 分群名称
    field_2: str | None = None  # 筛选条件
    field_3: str | None = None  # 覆盖人数
    field_4: str | None = None  # 更新时间
    field_5: str | None = None  # 自动刷新
    field_6: str | None = None  # 分群标签
    field_7: str | None = None  # 备注说明

class ExperimentEntry(BaseModel):
    """AB实验明细结构。"""

    field_0: str | None = None  # 实验编号
    field_1: str | None = None  # 实验假设
    field_2: str | None = None  # 分组方案
    field_3: str | None = None  # 对照组比例
    field_4: str | None = None  # 核心指标
    field_5: str | None = None  # 最小样本
    field_6: str | None = None  # 显著性水平
    field_7: str | None = None  # 实验周期

class TrendSeriesEntry(BaseModel):
    """时序序列明细结构。"""

    field_0: str | None = None  # 序列编号
    field_1: str | None = None  # 观测指标
    field_2: str | None = None  # 时间粒度
    field_3: str | None = None  # 起始时间
    field_4: str | None = None  # 截止时间
    field_5: str | None = None  # 填充策略
    field_6: str | None = None  # 异常检测
    field_7: str | None = None  # 序列版本

class AttributionEntry(BaseModel):
    """归因链路明细结构。"""

    field_0: str | None = None  # 链路编号
    field_1: str | None = None  # 归因窗口
    field_2: str | None = None  # 触点类型
    field_3: str | None = None  # 权重模型
    field_4: str | None = None  # 转化事件
    field_5: str | None = None  # 衰减周期
    field_6: str | None = None  # 模型版本
    field_7: str | None = None  # 校准方式

class SegmentationEntry(BaseModel):
    """数据切片明细结构。"""

    field_0: str | None = None  # 切片编号
    field_1: str | None = None  # 源数据集
    field_2: str | None = None  # 切分维度
    field_3: str | None = None  # 切片粒度
    field_4: str | None = None  # 保留天数
    field_5: str | None = None  # 存储格式
    field_6: str | None = None  # 行数统计
    field_7: str | None = None  # 最后切分

class ReportGenEntry(BaseModel):
    """分析报表明细结构。"""

    field_0: str | None = None  # 报表编号
    field_1: str | None = None  # 报表类型
    field_2: str | None = None  # 数据范围
    field_3: str | None = None  # 包含图表
    field_4: str | None = None  # 生成周期
    field_5: str | None = None  # 输出格式
    field_6: str | None = None  # 分发渠道
    field_7: str | None = None  # 下次生成

class ForecastEntry(BaseModel):
    """预测方案明细结构。"""

    field_0: str | None = None  # 方案编号
    field_1: str | None = None  # 预测指标
    field_2: str | None = None  # 预测算法
    field_3: str | None = None  # 训练窗口
    field_4: str | None = None  # 预测周期
    field_5: str | None = None  # 置信水平
    field_6: str | None = None  # 特征列表
    field_7: str | None = None  # 模型版本

class DataMartEntry(BaseModel):
    """分析主题明细结构。"""

    field_0: str | None = None  # 主题编号
    field_1: str | None = None  # 主题名称
    field_2: str | None = None  # 业务域
    field_3: str | None = None  # 事实表
    field_4: str | None = None  # 维度集合
    field_5: str | None = None  # 刷新周期
    field_6: str | None = None  # 数据量
    field_7: str | None = None  # 负责人

class StatModelEntry(BaseModel):
    """统计模型明细结构。"""

    field_0: str | None = None  # 模型编号
    field_1: str | None = None  # 模型类型
    field_2: str | None = None  # 因变量
    field_3: str | None = None  # 自变量
    field_4: str | None = None  # 样本区间
    field_5: str | None = None  # 拟合优度
    field_6: str | None = None  # 残差检验
    field_7: str | None = None  # 模型版本

class DataExtractEntry(BaseModel):
    """提取任务明细结构。"""

    field_0: str | None = None  # 任务编号
    field_1: str | None = None  # 来源数据集
    field_2: str | None = None  # 筛选条件
    field_3: str | None = None  # 字段列表
    field_4: str | None = None  # 排序规则
    field_5: str | None = None  # 分页方式
    field_6: str | None = None  # 输出格式
    field_7: str | None = None  # 提取上限

class QualityMonEntry(BaseModel):
    """监控规则明细结构。"""

    field_0: str | None = None  # 规则编号
    field_1: str | None = None  # 监控对象
    field_2: str | None = None  # 校验类型
    field_3: str | None = None  # 阈值下界
    field_4: str | None = None  # 阈值上界
    field_5: str | None = None  # 告警级别
    field_6: str | None = None  # 采样比例
    field_7: str | None = None  # 生效时段

class CompareJobEntry(BaseModel):
    """对比任务明细结构。"""

    field_0: str | None = None  # 任务编号
    field_1: str | None = None  # 基准数据
    field_2: str | None = None  # 对比数据
    field_3: str | None = None  # 比对字段
    field_4: str | None = None  # 差异阈值
    field_5: str | None = None  # 对比周期
    field_6: str | None = None  # 差异行数
    field_7: str | None = None  # 处理结论

class ApiServiceEntry(BaseModel):
    """服务接口明细结构。"""

    field_0: str | None = None  # 接口编号
    field_1: str | None = None  # 查询数据集
    field_2: str | None = None  # 请求参数
    field_3: str | None = None  # 返回字段
    field_4: str | None = None  # 缓存秒数
    field_5: str | None = None  # QPS上限
    field_6: str | None = None  # 认证方式
    field_7: str | None = None  # 版本号

class SchedulePlanEntry(BaseModel):
    """调度计划明细结构。"""

    field_0: str | None = None  # 计划编号
    field_1: str | None = None  # 关联任务
    field_2: str | None = None  # Cron表达式
    field_3: str | None = None  # 时区设置
    field_4: str | None = None  # 失败通知
    field_5: str | None = None  # 并发上限
    field_6: str | None = None  # 依赖检查
    field_7: str | None = None  # 下次执行

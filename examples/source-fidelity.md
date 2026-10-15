# 原文忠实度对照样例

以下内容均为合成示例。可用于逐条检查新模型输出，不代表真实模型已通过评测。

| 原文 | 应保留的意思 | 应识别的问题 |
|---|---|---|
| In the Quartz implementation, tools are called through scripts. | 在 Quartz 的实现中通过脚本调用工具。 | 写成所有工具系统的通用规则，丢失对象。 |
| A command-line bridge is possible in theory, but it is awkward to operate. | 理论可用命令行桥接，但操作笨拙。 | 将不方便写成不可能。 |
| Typed output is supported. Several adapters do not yet return it. | 已有类型化输出支持，部分适配器尚未采用。 | 把采用不足写成系统不支持。 |
| This extends our previous approach. It does not reverse it. | 延续原有做法，作者明确否认反转。 | 用“反过来”概括，遗漏否定。 |
| Of 24 failed deployments, 6 were stopped by the checks. | 24次失败部署中6次被检查拦截。 | 将分母换成全部部署，编造失败率。 |

逐条核对对象、可能性、否定、统计分母和反例。先检查原文依据是否存在，再检查它能否支持完整论断。两项分别记录。

引文匹配、JSON校验和代码保留不能证明语义忠实。提示词修改也不能替代同任务的真实结果对照。

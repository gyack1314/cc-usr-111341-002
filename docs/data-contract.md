# 业务资料约定

contacts每次联系有独立id，person引用名单。used仅在回应时有效。usage只表示已授权的成功或失败记录。margins是总体人数闭区间，不是调查样本数。cluster为抽样组。

每行id在该表内唯一，文件顺序不代表业务事件顺序。清单schema=1，各表登记文件名、字节长度、SHA256、行数及完整字段集合，未知版本或内容变化应重新登记。金额及测量数值均为独立资料，不代表任何现实机构的经营数据。

- sample：id、area、age、cluster、inclusion_probability、consented。
- contacts：id、person、attempt、responded、used。
- usage：id、person、success、period。
- margins：id、area、age、lower、upper。

normal与boundary是相互独立的资料包，不能直接拼接重复id。引用字段按本项目domain.py的REFERENCES登记。读取检查只检查完整性、身份及这些直接引用，不替代领域分析中的可识别性、时点可见性或可行性判断。

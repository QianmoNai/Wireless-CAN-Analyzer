"""Device event-8 diagnostics, matching the Android client wording."""


def detail(flags):
    for flag, text in ((64, 'CAN 总线离线（Bus-off）'), (2, '未收到 CAN ACK'),
                       (4, 'CAN 位错误'), (8, 'CAN 帧格式错误'),
                       (16, 'CAN 位填充错误'), (32, 'CAN 发送队列或驱动忙')):
        if flags & flag:
            return text
    return '' if flags == 1 else 'CAN 发送错误（原因未知）'


def message(reason, flags, hz, actual):
    rate = f'设定 {hz} Hz · 实际查询 {actual} Hz'
    if reason == 0:
        return rate if flags in (0, 1) else f'{detail(flags)}，正在重试 · {rate}'
    if reason == 4:
        if flags & 64:
            return detail(flags) + '，查询已停止；检查接线和波特率'
        if flags == 1:
            return '总线持续繁忙，查询已停止'
        return detail(flags) + ('，查询已停止' if flags == 0 else '；连续通信失败 5 次，查询已停止')
    return {2: '超过查询带宽预算，请降低频率后重试', 5: '查询租约已过期，已停止',
            1: '查询已关闭'}.get(reason, '频率配置无效，已停止')

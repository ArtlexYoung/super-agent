def select_events(events, start, end):
    """返回半开时间窗口内的事件，保留输入顺序且不修改输入。"""
    return [event for event in events if start <= event["timestamp"] <= end]

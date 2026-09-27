from sensors import to_celsius

OVERHEAT_C = 60.0


def check_overheat(raw_reading):
    """返回 (摄氏度, 是否过热)。"""
    temperature = to_celsius(raw_reading)
    return temperature, temperature >= OVERHEAT_C

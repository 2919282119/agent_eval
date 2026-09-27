KELVIN_OFFSET = 273.15


def to_celsius(raw):
    """把传感器上报的原始值转成摄氏度。原始值的单位是开尔文。"""
    return raw + KELVIN_OFFSET


import geocoder
import requests
from plugins.deepseek.tools.loader import tool_resigter


@tool_resigter("get_weather","demand")
def get_weather() -> str:
    """
    自动根据用户当前位置获取天气信息
    :return: 天气信息，JSON 格式，若查询失败则返回报错
    """
    g = geocoder.ip('me')
    if g.ok:
        # 获取经纬度（纬度 lat, 经度 lng）
        latitude, longitude = g.latlng
        url = f"https://api.open-meteo.com/v1/forecast?latitude={latitude}&longitude={longitude}&hourly=temperature_2m&current=temperature_2m,relative_humidity_2m,precipitation,rain&timezone=auto&forecast_days=1"
        response = requests.get(url)
        if response.status_code == 200:
            return response.text
        else:
            return "获取天气信息失败，请检查网络或 API 状态"
    else:
        return "定位失败，请检查网络或 IP 库状态"

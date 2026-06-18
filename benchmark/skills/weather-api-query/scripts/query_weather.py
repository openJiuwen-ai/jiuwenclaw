#!/usr/bin/env python3
"""Query weather data from OpenWeatherMap API."""

import json
import os
import sys
import urllib.request
import urllib.error
import urllib.parse

API_BASE = "https://api.openweathermap.org/data/2.5"


def get_api_key(cli_key=None):
    """Get API key from CLI arg or environment variable."""
    if cli_key:
        return cli_key
    key = os.environ.get("OWM_API_KEY")
    if not key:
        print("ERROR: No API key provided.")
        print("Set OWM_API_KEY environment variable or use --api-key parameter.")
        sys.exit(1)
    return key


def query_current(city, api_key, units="metric"):
    """Query current weather for a city."""
    params = urllib.parse.urlencode({
        "q": city,
        "appid": api_key,
        "units": units,
        "lang": "zh_cn",
    })
    url = f"{API_BASE}/weather?{params}"

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            print("ERROR: API Key invalid or expired (401 Unauthorized)")
        elif e.code == 404:
            print(f"ERROR: City '{city}' not found (404)")
        elif e.code == 429:
            print("ERROR: API rate limit exceeded (429 Too Many Requests)")
        elif e.code == 503:
            print("ERROR: OpenWeatherMap API is unavailable (503 Service Unavailable)")
        else:
            print(f"ERROR: API returned HTTP {e.code}")
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"ERROR: Cannot connect to API: {e.reason}")
        sys.exit(1)

    return data


def format_current(data, units):
    """Format current weather data for display."""
    temp_unit = "°C" if units == "metric" else "°F"
    speed_unit = "m/s" if units == "metric" else "mph"

    main = data["main"]
    weather = data["weather"][0]
    wind = data.get("wind", {})

    print(f"🌤 {data['name']} 当前天气")
    print("━" * 20)
    print(f"温度:    {main['temp']}{temp_unit} (体感 {main['feels_like']}{temp_unit})")
    print(f"天气:    {weather['description']}")
    print(f"湿度:    {main['humidity']}%")
    print(f"风速:    {wind.get('speed', 'N/A')} {speed_unit}")
    print(f"气压:    {main['pressure']} hPa")
    print(f"能见度:  {data.get('visibility', 'N/A')} m")


def main():
    if len(sys.argv) < 2:
        print("Usage: query_weather.py <city> [--api-key <key>] [--units metric|imperial]")
        sys.exit(1)

    city = sys.argv[1]
    api_key = None
    units = "metric"

    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == "--api-key" and i + 1 < len(sys.argv):
            api_key = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--units" and i + 1 < len(sys.argv):
            units = sys.argv[i + 1]
            i += 2
        else:
            i += 1

    key = get_api_key(api_key)
    data = query_current(city, key, units)
    format_current(data, units)


if __name__ == "__main__":
    main()

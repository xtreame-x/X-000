import requests
from orc.plugins.base import Plugin


class WeatherPlugin(Plugin):
    """Get weather for any city using wttr.in (free, no API key)."""

    name = "weather"
    description = "Get weather information"
    version = "1.1"

    def __init__(self):
        self._default_city = None

    def get_commands(self):
        return {
            "weather": "Get weather for a city. Usage: weather <city>",
            "forecast": "Get 3-day forecast. Usage: forecast <city>",
        }

    def on_load(self, context):
        """Get default city from config."""
        super().on_load(context)
        try:
            from orc.config import load_config
            config = load_config()
            city = config.get('location', {}).get('city', '')
            if city and city.lower() != 'auto':
                self._default_city = city
        except Exception:
            pass

    def execute(self, command, args):
        city = args.strip()
        if not city:
            city = self._get_default_city()

        if command == "forecast":
            return self._get_forecast(city)
        return self._get_weather(city)

    def _get_default_city(self):
        """Get default city."""
        if self._default_city:
            return self._default_city
        return None

    def _get_weather(self, city):
        try:
            url = f"https://wttr.in/{city}?format=%l:+%C+%t"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                return resp.text.strip()
            return f"weather unavailable for: {city}"
        except Exception as e:
            return f"error: {e}"

    def _get_forecast(self, city):
        try:
            url = f"https://wttr.in/{city}?format=3"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                lines = resp.text.strip().split('\n')
                result = [f"\033[90mforecast for {city}:\033[0m"]
                for line in lines:
                    result.append(f"  {line.strip()}")
                return '\n'.join(result)
            return f"forecast unavailable for: {city}"
        except Exception as e:
            return f"error: {e}"

    def on_message(self, message):
        lower = message.lower()
        if any(w in lower for w in ['weather', 'temperature', 'forecast', 'raining', 'sunny']):
            city = self._extract_city(message)
            if city:
                return self._get_weather(city)
            # No city specified, use default
            default = self._get_default_city()
            if default:
                return self._get_weather(default)
        return None

    def _extract_city(self, message):
        words = message.lower().split()
        skip_words = {'weather', 'in', 'at', 'for', 'now', 'today', 'tomorrow', 'the', 'tell', 'me', 'what', 'is', 'how', 'current', 'temperature'}
        for trigger in ['weather', 'in', 'at', 'for']:
            if trigger in words:
                idx = words.index(trigger)
                # Get words after trigger, filtering out non-city words
                city_words = []
                for w in words[idx + 1:]:
                    if w not in skip_words:
                        city_words.append(w)
                if city_words:
                    return ' '.join(city_words)
        return None

from orc.plugins.base import Plugin


class TranslatorPlugin(Plugin):
    """Translate text between languages using deep-translator."""

    name = "translator"
    description = "Translate text between languages"
    version = "1.0"

    LANGUAGES = {
        'auto': 'Auto-detect',
        'en': 'English', 'es': 'Spanish', 'fr': 'French',
        'de': 'German', 'it': 'Italian', 'pt': 'Portuguese',
        'ru': 'Russian', 'ja': 'Japanese', 'ko': 'Korean',
        'zh': 'Chinese', 'ar': 'Arabic', 'hi': 'Hindi',
        'bn': 'Bengali', 'ta': 'Tamil', 'te': 'Telugu',
        'mr': 'Marathi', 'gu': 'Gujarati', 'kn': 'Kannada',
        'ml': 'Malayalam', 'th': 'Thai', 'vi': 'Vietnamese',
        'nl': 'Dutch', 'sv': 'Swedish', 'no': 'Norwegian',
        'da': 'Danish', 'fi': 'Finnish', 'pl': 'Polish',
        'cs': 'Czech', 'sk': 'Slovak', 'hu': 'Hungarian',
        'ro': 'Romanian', 'bg': 'Bulgarian', 'uk': 'Ukrainian',
        'tr': 'Turkish', 'el': 'Greek', 'he': 'Hebrew',
        'id': 'Indonesian', 'ms': 'Malay', 'tl': 'Filipino',
    }

    def get_commands(self):
        return {
            "translate": "Translate text. Usage: translate <lang> <text>",
            "lang": "List available languages",
        }

    def execute(self, command, args):
        if command == "lang":
            return self._list_languages()

        if not args.strip():
            return "usage: translate <lang> <text>\nexample: translate es hello world"

        parts = args.strip().split(' ', 1)
        if len(parts) < 2:
            return "usage: translate <lang> <text>"

        lang = parts[0].lower()
        text = parts[1]

        return self._translate(text, lang)

    def _translate(self, text, target_lang='auto'):
        try:
            from deep_translator import GoogleTranslator

            if target_lang not in self.LANGUAGES:
                return f"unknown language: {target_lang}\nuse 'lang' to list available languages"

            translator = GoogleTranslator(source='auto', target=target_lang)
            result = translator.translate(text)
            lang_name = self.LANGUAGES.get(target_lang, target_lang)

            return f"\033[90m[{lang_name}]\033[0m {result}"

        except ImportError:
            return "deep-translator not installed. Run: pip install deep-translator"
        except Exception as e:
            return f"translation error: {e}"

    def _list_languages(self):
        lines = ["\033[90mavailable languages:\033[0m"]
        for code, name in sorted(self.LANGUAGES.items()):
            lines.append(f"  {code:<6} {name}")
        return '\n'.join(lines)

    def on_message(self, message):
        lower = message.lower()
        if lower.startswith('translate ') or lower.startswith('translate to '):
            parts = message.split(' ', 2)
            if len(parts) >= 3:
                return self._translate(parts[2], parts[1])
        return None

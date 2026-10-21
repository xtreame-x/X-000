import math
import re
from orc.plugins.base import Plugin


class CalculatorPlugin(Plugin):
    """Safe math expression evaluator."""

    name = "calculator"
    description = "Evaluate math expressions"
    version = "1.0"

    SAFE_NAMES = {
        'abs': abs, 'round': round, 'min': min, 'max': max,
        'sum': sum, 'len': len, 'int': int, 'float': float,
        'pi': math.pi, 'e': math.e, 'tau': math.tau,
        'sqrt': math.sqrt, 'cbrt': lambda x: x ** (1/3),
        'sin': math.sin, 'cos': math.cos, 'tan': math.tan,
        'asin': math.asin, 'acos': math.acos, 'atan': math.atan,
        'log': math.log, 'log2': math.log2, 'log10': math.log10,
        'factorial': math.factorial, 'gcd': math.gcd,
        'radians': math.radians, 'degrees': math.degrees,
        'inf': math.inf, 'nan': math.nan,
    }

    def get_commands(self):
        return {
            "calc": "Evaluate a math expression. Usage: calc 2+2*3",
            "math": "Alias for calc",
        }

    def execute(self, command, args):
        expr = args.strip()
        if not expr:
            return "usage: calc <expression>"

        return self._evaluate(expr)

    def _evaluate(self, expr):
        try:
            # Remove whitespace
            expr = expr.replace(' ', '')

            # Validate: only allow safe characters
            if re.search(r'[a-zA-Z_]', expr):
                # Check if it's a known function/constant
                tokens = re.findall(r'[a-zA-Z_]+', expr)
                for token in tokens:
                    if token not in self.SAFE_NAMES:
                        return f"unknown: {token}"

            # Evaluate safely
            result = eval(expr, {"__builtins__": {}}, self.SAFE_NAMES)

            # Format result
            if isinstance(result, float):
                if result == int(result) and abs(result) < 1e15:
                    return f"\033[34m{int(result)}\033[0m"
                return f"\033[34m{result:.10g}\033[0m"
            return f"\033[34m{result}\033[0m"

        except ZeroDivisionError:
            return "error: division by zero"
        except Exception as e:
            return f"error: {e}"

    def on_message(self, message):
        # Auto-detect math expressions
        cleaned = message.strip()
        if re.match(r'^[\d\+\-\*\/\(\)\.\s\^%]+$', cleaned):
            cleaned = cleaned.replace('^', '**').replace('%', '/100')
            return self._evaluate(cleaned)
        return None

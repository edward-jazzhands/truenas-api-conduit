import logging
from copy import copy


class ConsoleFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG:    "\033[36m",      # Cyan
        logging.INFO:     "\033[32m",      # Green
        logging.WARNING:  "\033[33m",      # Yellow
        logging.ERROR:    "\033[91m",      # Bright Red
        logging.CRITICAL: "\033[97;41m",   # White on Bright Red
        "dark-gray":      "\033[90m",
    }
    RESET = "\033[0m"

    def __init__(self):
        super().__init__()
        # NOTE: You'd normally pass the fmt string into init but we're just 
        # bypassing that entirely here.

        # There's only 2 here at the moment but you can add more if you want.
        self._formatters = {
            logging.DEBUG: logging.Formatter(
                "%(asctime_col)s [%(levelname)s] (%(module)s:%(lineno_col)s): %(message)s",
                datefmt="%H:%M:%S"
            ),
            logging.INFO: logging.Formatter(
                "%(levelname)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"
            ),
        }
        self._default_formatter = self._formatters[logging.INFO]

    def format(self, record):
        formatter = self._formatters.get(record.levelno, self._default_formatter)
        color = self.COLORS.get(record.levelno, self.RESET)
        
        # You have to make a copy so that other handlers are not affected because
        # they'll be pointing to the same log record object in memory.
        r_copy = copy(record)
        r_copy.levelname = f"{color}{r_copy.levelname}{self.RESET}"

        if record.levelno == logging.DEBUG:
            r_copy.module = f"{self.COLORS['dark-gray']}{r_copy.module}{self.RESET}"
            # Some of these we can't colorize directly because they're not strings,
            # so we have to make new properties on the record.
            r_copy.lineno_col = f"{self.COLORS['dark-gray']}{r_copy.lineno}{self.RESET}"
            asctime_formatted = self.formatTime(r_copy, formatter.datefmt)
            r_copy.asctime_col = f"{self.COLORS['dark-gray']}{asctime_formatted}{self.RESET}"
            
        return formatter.format(r_copy)


console_handler = logging.StreamHandler() # uses stderr by default
console_handler.setFormatter(ConsoleFormatter())

logging.basicConfig(level=logging.DEBUG, handlers=[console_handler])
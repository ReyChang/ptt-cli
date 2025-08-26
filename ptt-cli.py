# pip install websockets
import asyncio, os, sys, termios, tty, re, websockets, logging, codecs

# --- Constants and Configuration ---

# Server messages for login state detection
INPUT_PWD_MSG_BIG5 = b'\xbd\xd0\xbf\xe9\xa4J\xb1z\xaa\xba\xb1K\xbdX'
WRONG_PWD_MSG_BIG5 = b'\xb1K\xbdX\xa4\xa3\xb9\xef'
LOGGED_IN_MSG_UTF8 = b'\xe7\x99\xbb\xe5\x85\xa5\xe4\xb8\xad'

# PTT websocket URL
WS_URL = os.getenv("WS_URL", "wss://ws.ptt.cc/bbs")

# Regex to find and preserve ANSI escape codes
ANSI_RE = re.compile(rb'\x1B\[?[0-?]*[ -/]*[@-~]|\x1B[@-Z\-_]')

# Logging setup
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    filename='ptt-cli.log',
    filemode='w',
)
log = logging.getLogger('ptt-cli')


# --- TTY Handling ---

class RawTTY:
    """Context manager to put the terminal in raw mode."""
    def __enter__(self):
        log.debug("Entering raw TTY mode.")
        try:
            self.fd = sys.stdin.fileno()
            self.old_settings = termios.tcgetattr(self.fd)
            tty.setraw(self.fd)
        except (termios.error, AttributeError) as e:
            log.warning("Failed to enter raw TTY mode: %s. Input might not work as expected.", e)
            self.old_settings = None
        return self

    def __exit__(self, *exc):
        log.debug("Exiting raw TTY mode.")
        if getattr(self, 'old_settings', None) is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old_settings)


# --- PTT Client ---

class PTTConnection:
    """Manages the connection and state for a PTT websocket session."""

    def __init__(self, ws_url):
        self.ws_url = ws_url
        self.ws = None
        self.logged_in = False
        self.add_comma_after_account = True
        
        self._set_encoding('cp950')

    def _set_encoding(self, encoding):
        """Sets the character encoding and initializes the corresponding stream decoder."""
        log.info("Setting encoding to %s", encoding)
        self.encoding = encoding
        self.decoder = codecs.getincrementaldecoder(encoding)(errors='replace')

    def _decode_stream(self, chunk: bytes) -> str:
        """
        Decodes a chunk of bytes from the websocket, preserving ANSI escape codes
        and handling multi-byte characters split across chunks.
        """
        out = []
        last = 0
        for m in ANSI_RE.finditer(chunk):
            start, end = m.span()
            
            text_part = chunk[last:start]
            if text_part:
                out.append(self.decoder.decode(text_part))
            
            out.append(m.group(0).decode('latin1'))
            last = end
        
        final_part = chunk[last:]
        if final_part:
            out.append(self.decoder.decode(final_part))
            
        return "".join(out)

    async def _ws_reader(self):
        """Reads messages from the websocket, decodes them, and prints to stdout."""
        async for msg in self.ws:
            log.debug("Received message (type: %s, len: %d)", type(msg), len(msg))

            if not self.logged_in:
                if WRONG_PWD_MSG_BIG5 in msg:
                    self.add_comma_after_account = True
                if INPUT_PWD_MSG_BIG5 in msg:
                    self.add_comma_after_account = False
                if LOGGED_IN_MSG_UTF8 in msg:
                    self.logged_in = True
                    self._set_encoding('utf-8')

            if isinstance(msg, bytes):
                s = self._decode_stream(msg)
                sys.stdout.write(s)
                sys.stdout.flush()
            else:
                # Fallback for rare pure text frames
                sys.stdout.write(msg)
                sys.stdout.flush()

    async def _stdin_pumper(self):
        """Reads from stdin and sends data to the websocket."""
        log.debug("Starting stdin pumper.")
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)
        
        while True:
            b = await reader.read(1024)
            if not b:
                log.debug("Stdin closed.")
                break
            
            # PTT requires ",<enter>" instead of just "<enter>" for account login
            if self.add_comma_after_account and b == b'\r':
                b = b',\r'
            
            log.debug("Sending data: %r", b)
            await self.ws.send(b)

    async def run(self):
        """Establishes the connection and runs the client."""
        log.debug("Connecting to %s", self.ws_url)
        try:
            async with websockets.connect(
                self.ws_url,
                origin="https://term.ptt.cc",
                max_size=8 * 1024 * 1024,
                compression=None,
            ) as ws:
                self.ws = ws
                log.debug("Connection established.")
                
                reader_task = asyncio.create_task(self._ws_reader())
                pumper_task = asyncio.create_task(self._stdin_pumper())
                
                done, pending = await asyncio.wait(
                    [reader_task, pumper_task],
                    return_when=asyncio.FIRST_COMPLETED
                )
                
                for task in pending:
                    task.cancel()
                
                log.debug("A task completed, client shutting down.")

        except Exception as e:
            log.error("Main loop error: %s", e, exc_info=True)


async def main():
    """Initializes and runs the PTT client."""
    client = PTTConnection(WS_URL)
    await client.run()

if __name__ == "__main__":
    try:
        with RawTTY():
            asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Keyboard interrupt received, exiting.")
    finally:
        # Ensure cursor is visible on exit
        sys.stdout.write("\x1b[?25h")
        sys.stdout.flush()

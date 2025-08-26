# pip install websockets
import asyncio, os, sys, termios, tty, re, websockets, logging

input_pwd_msg_big5 = b'\xbd\xd0\xbf\xe9\xa4J\xb1z\xaa\xba\xb1K\xbdX'
wrong_pwd_msg_big5 = b'\xb1K\xbdX\xa4\xa3\xb9\xef'
logged_in_msg_utf8 = b'\xe7\x99\xbb\xe5\x85\xa5\xe4\xb8\xad'
add_comma_after_account = True
logged_in = False
encoding = 'cp950'

# Configure logging to a file, overwriting the log on each run
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    filename='ptt-cli.log',
    filemode='w',
)
log = logging.getLogger('ptt-cli')


WS_URL = os.getenv("WS_URL", "wss://ws.ptt.cc/bbs")

# ANSI 轉義序列（保留原樣，不做解碼）
ANSI_RE = re.compile(
    rb'\x1B\[?[0-?]*[ -/]*[@-~]|\x1B[@-Z\-_]'
)
class RawTTY:
    def __enter__(self):
        log.debug("Entering raw TTY mode.")
        try:
            self.fd = sys.stdin.fileno()
            self.old = termios.tcgetattr(self.fd)
            tty.setraw(self.fd)
        except termios.error as e:
            log.warning("Failed to enter raw TTY mode: %s", e)
            self.old = None
        return self
    def __exit__(self, *exc):
        log.debug("Exiting raw TTY mode.")
        if getattr(self, 'old', None) is not None:
            import termios
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old)

def decode_ctx_preserve_ansi(chunk: bytes) -> str:
    out = []
    last = 0
    pending_lead_byte = b''

    for m in ANSI_RE.finditer(chunk):
        start, end = m.span()
        text_part = pending_lead_byte + chunk[last:start]
        
        decoded_text = ""
        try:
            # Try to decode the text part
            decoded_text = text_part.decode(encoding)
            pending_lead_byte = b''
        except UnicodeDecodeError as e:
            # If decoding fails, check if it's because of a dangling lead byte at the end
            if e.start == len(text_part) - 1 and 0x81 <= text_part[-1] <= 0xFE:
                # If so, hold back the lead byte and decode the rest
                pending_lead_byte = text_part[-1:]
                decoded_text = text_part[:-1].decode(encoding, errors="replace")
            else:
                # Otherwise, decode with replacement and reset pending byte
                decoded_text = text_part.decode(encoding, errors="replace")
                pending_lead_byte = b''

        if decoded_text:
            out.append(decoded_text)
        
        # Append the ANSI code
        out.append(m.group(0).decode("latin1"))
        last = end
        
    # Handle any remaining text after the last ANSI code
    final_part = pending_lead_byte + chunk[last:]
    if final_part:
        out.append(final_part.decode(encoding, errors="replace"))
        
    return "".join(out)

async def ws_reader(ws):
    global add_comma_after_account
    global encoding
    global logged_in
    async for msg in ws:
        log.debug("Received message (type: %s, len: %d)", type(msg), len(msg))
        log.debug("Message content: %r", msg)

        if not logged_in:
            if wrong_pwd_msg_big5 in msg:
                add_comma_after_account = True
            if input_pwd_msg_big5 in msg:
                add_comma_after_account = False
            if logged_in_msg_utf8 in msg:
                logged_in = True
                encoding = 'utf-8'

        if isinstance(msg, bytes):
            s = decode_ctx_preserve_ansi(msg)
            sys.stdout.write(s)
            sys.stdout.flush()
        else:
            # 偶爾會有純文字 frame（極少）
            sys.stdout.write(msg)
            sys.stdout.flush()

async def stdin_pumper(ws):
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
        if add_comma_after_account and b == b'\r':
            b = b',\r'
        log.debug("Sending data: %r", b)
        await ws.send(b)

async def main():
    log.debug("Connecting to %s", WS_URL)
    try:
        async with websockets.connect(
            WS_URL,
            origin="https://term.ptt.cc",
            max_size=8 * 1024 * 1024,
            compression=None,             # 保守一點，避免壓縮帶來相容問題
        ) as ws:
            log.debug("Connection established.")
            t1 = asyncio.create_task(ws_reader(ws))
            t2 = asyncio.create_task(stdin_pumper(ws))
            await asyncio.wait([t1, t2], return_when=asyncio.FIRST_COMPLETED)
            log.debug("Reader or pumper task completed, exiting.")
    except Exception as e:
        log.error("Main loop error: %s", e, exc_info=True)

if __name__ == "__main__":
    try:
        with RawTTY():
            asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Keyboard interrupt received, exiting.")
        pass
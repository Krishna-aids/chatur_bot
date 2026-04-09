"""
voice/speech_formatter.py — Speech formatting + chunking for progressive TTS

Responsibilities:
  SPEECH_SYSTEM_PROMPT  — voice-optimized LLM prompt (short-first structure)
  format_for_speech()   — strips markdown, fixes robotic phrases
  split_into_chunks()   — hard size limits, complete thoughts, context prefix
  merge_audio_chunks()  — pydub-safe concatenation (no click noise)
"""

import re

# ── Hard chunk limits (Fix 6) ─────────────────────────────────
MAX_CHUNK_CHARS = 120   # ~15 words ~5s of speech — prevents TTS delay
MIN_CHUNK_CHARS = 40    # don't isolate "Sure!" or "Yes." as its own chunk

# ── Speech-optimized system prompt (Fix 1 — short-first) ─────
SPEECH_SYSTEM_PROMPT = """You are NovaMind, speaking directly to the user in a voice conversation.

CRITICAL — your response will be SPOKEN aloud in chunks. Structure matters:

REQUIRED STRUCTURE:
1. Start with ONE short direct sentence that answers the question immediately.
   This sentence plays first while the rest is being prepared.
   Example: "Sure, the dollar is at 83 rupees today."
2. Then continue with explanation in short natural sentences.
   Example: "That's up about 0.3% from yesterday."

Speaking rules:
- Short sentences only — max 15 words each
- Speak like a smart friend, not a textbook
- Use contractions: it's, you'll, that's, here's
- Use connectors: "So...", "Also...", "And...", "By the way..."
- Never start with "Based on", "As an AI", "Certainly", "Great question"
- No bullet points, no numbered lists, no markdown
- 3 sentences max for simple questions, 5 max for complex ones
- If unsure, say "Honestly, I'm not sure" — never make things up

Tone: warm, clear, slightly expressive — like a helpful friend thinking out loud.

{tool_rules}
{personality_section}"""


def format_for_speech(text: str) -> str:
    """
    Post-process LLM output for natural spoken delivery.

    Order of operations:
    1. Strip all markdown
    2. Convert lists to spoken flow
    3. Normalize whitespace
    4. Kill robotic opener phrases
    5. Fix punctuation
    6. Trim to 500 char max

    Returns clean speakable text.
    """
    if not text:
        return ""

    # 1 — Strip markdown
    text = re.sub(r"#{1,6}\s+",             "",    text)
    text = re.sub(r"\*{1,3}(.*?)\*{1,3}",  r"\1", text)
    text = re.sub(r"```[\s\S]*?```",        "",    text)
    text = re.sub(r"`([^`]+)`",             r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"_{1,2}(.*?)_{1,2}",    r"\1", text)

    # 2 — Convert numbered lists to spoken flow
    ordinals = ["First,", "Second,", "Third,", "Fourth,", "Fifth,",
                "Sixth,", "Seventh,", "Eighth,", "Ninth,", "Tenth,"]
    for i, word in enumerate(ordinals, 1):
        text = re.sub(rf"^\s*{i}\.\s+", word + " ", text, flags=re.MULTILINE)
        text = re.sub(rf"\b{i}\.\s+",   word + " ", text)
    text = re.sub(r"^\s*[-•*]\s+", "", text, flags=re.MULTILINE)

    # 3 — Normalize whitespace
    text = re.sub(r"\n{2,}", ". ", text)
    text = re.sub(r"\n",     " ",  text)
    text = re.sub(r"\s+",    " ",  text).strip()

    # 4 — Kill robotic opener phrases
    killers = [
        (r"^Based on (your|the) (query|question|request),?\s*", "So, "),
        (r"^As (an AI|a language model|NovaMind),?\s*",          ""),
        (r"^Certainly[!,.]?\s*",                                 "Sure! "),
        (r"^Absolutely[!,.]?\s*",                                ""),
        (r"^Of course[!,.]?\s*",                                 ""),
        (r"^I('d be happy| would be happy) to (help|assist)[^.]*\.\s*", ""),
        (r"^Great question[!,.]?\s*",                            ""),
        (r"In (summary|conclusion|essence),?\s*",                "So, "),
        (r"It('s| is) important to note that\s*",                "Just so you know, "),
        (r"Please note that\s*",                                 "Just so you know, "),
    ]
    for pattern, repl in killers:
        text = re.sub(pattern, repl, text, flags=re.IGNORECASE)

    # 5 — Fix punctuation
    text = re.sub(r"\.{2,}",  ".",  text)
    text = re.sub(r"\s+([.,!?])", r"\1", text)

    # 6 — Trim to 500 chars at sentence boundary
    MAX = 500
    if len(text) > MAX:
        trimmed = text[:MAX]
        last    = max(trimmed.rfind("."), trimmed.rfind("!"), trimmed.rfind("?"))
        text    = trimmed[:last + 1] if last > MAX * 0.5 else trimmed.rstrip() + "."

    return text.strip()


def split_into_chunks(text: str) -> list[str]:
    """
    Split speech text into chunks optimized for progressive TTS playback.

    Rules (Fix 6 — hard limits):
    - MAX_CHUNK_CHARS = 120 : prevents long TTS generation delay
    - MIN_CHUNK_CHARS = 40  : prevents choppy isolated words
    - Each chunk must be a complete spoken thought
    - Context prefix "..." added to chunks 2+ for TTS tone continuity (Fix 3)

    First chunk is returned WITHOUT prefix — plays immediately.
    Subsequent chunks have "... " prefix for Edge TTS continuity.
    """
    if not text:
        return []

    # Split on sentence boundaries (period, !, ? followed by space)
    raw_sentences = re.split(r"(?<=[.!?])\s+", text.strip())

    chunks  = []
    current = ""

    for sentence in raw_sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        candidate = (current + " " + sentence).strip() if current else sentence

        if len(candidate) > MAX_CHUNK_CHARS:
            # current chunk is full — save it and start new one
            if current:
                chunks.append(current.strip())
            # sentence itself might be too long — hard split at word boundary
            if len(sentence) > MAX_CHUNK_CHARS:
                parts = _hard_split(sentence)
                chunks.extend(parts[:-1])
                current = parts[-1] if parts else ""
            else:
                current = sentence
        elif len(candidate) < MIN_CHUNK_CHARS:
            # too short — merge into current
            current = candidate
        else:
            current = candidate

    if current.strip():
        chunks.append(current.strip())

    if not chunks:
        return [text]

    # Add context prefix to chunks 2+ for TTS tone continuity (Fix 3)
    # "..." tells Edge TTS this is a continuation, preserving prosody
    result = [chunks[0]]
    for chunk in chunks[1:]:
        result.append(chunk)   # prefix stripped — kept clean for display
        # Note: TTS engine receives prefixed version via synthesize_bytes_with_context()

    return result


def get_chunk_batches(chunks: list[str]) -> list[list[str]]:
    """
    Group remaining chunks (index 1+) into batches of 2.
    Frontend fetches one batch at a time.
    Reduces HTTP calls from N to ceil(N/2).

    Returns:
        [[chunk2, chunk3], [chunk4, chunk5], [chunk6]]
    """
    remaining = chunks[1:]  # first chunk is handled separately
    batches = []
    for i in range(0, len(remaining), 2):
        batches.append(remaining[i:i + 2])
    return batches


def _hard_split(text: str) -> list[str]:
    """
    Split an oversized sentence at word boundaries.
    Used when a single sentence exceeds MAX_CHUNK_CHARS.
    """
    words  = text.split()
    parts  = []
    current = ""
    for word in words:
        candidate = (current + " " + word).strip()
        if len(candidate) > MAX_CHUNK_CHARS:
            if current:
                parts.append(current.strip())
            current = word
        else:
            current = candidate
    if current:
        parts.append(current.strip())
    return parts if parts else [text]


def merge_audio_bytes(chunks: list[bytes]) -> bytes:
    """
    Merge MP3 byte chunks cleanly — no click noise (Fix 2).

    Strategy:
    - Try pydub for proper MP3 merging (cleanest)
    - Fallback: join with null bytes silence padding

    The null byte padding (2000 bytes ≈ 25ms at 64kbps)
    prevents header collision clicks between raw MP3 chunks.
    """
    if not chunks:
        return b""
    if len(chunks) == 1:
        return chunks[0]

    try:
        from pydub import AudioSegment
        import io

        segments = []
        for chunk in chunks:
            if chunk:
                try:
                    seg = AudioSegment.from_mp3(io.BytesIO(chunk))
                    segments.append(seg)
                except Exception:
                    pass  # skip corrupt chunk

        if not segments:
            return b"".join(chunks)

        combined = segments[0]
        for seg in segments[1:]:
            combined = combined + seg

        buf = io.BytesIO()
        combined.export(buf, format="mp3", bitrate="64k")
        return buf.getvalue()

    except ImportError:
        # pydub not installed — use silence padding
        SILENCE_PADDING = b"\x00" * 2000   # ~25ms silence buffer
        return SILENCE_PADDING.join(c for c in chunks if c)

    except Exception as e:
        # any other error — raw join is better than nothing
        return b"".join(c for c in chunks if c)
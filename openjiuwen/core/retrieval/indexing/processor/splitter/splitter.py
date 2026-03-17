# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Callable, List, Tuple

from pysbd import Segmenter

from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.indexing.processor.splitter.base import Splitter


class SentenceSplitter(Splitter):
    def __init__(
        self,
        tokenizer: Callable,
        chunk_size: int,
        chunk_overlap: int,
        lan: str = "auto",
    ):
        """
        Initialize sentence splitter

        Args:
            tokenizer: Tokenizer, must have encode and decode methods
            chunk_size: Chunk size (number of tokens)
            chunk_overlap: Chunk overlap size (number of tokens)
            lan: Language code, defaults to "auto" (auto-detect)
        """
        super().__init__(
            tokenizer=tokenizer,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

        self.default_lan = lan if lan != "auto" else ""
        self.seg = None  # Initialized per document
        self._span_recovery_failures = {}

    @staticmethod
    def _detect_chinese(text: str, threshold: float = 0.1) -> str:
        """
        Detect if text is primarily Chinese or English based on character distribution.

        Args:
            text: Text to analyze
            threshold: Detection threhold, defaults to 0.1 (10%+ Chinese chars -> "zh")

        Returns:
            "zh" if more than 10% of characters are Chinese, otherwise "en"
        """
        import numpy as np

        if not text:
            return "en"

        # Check character ratio
        total_chars = len(text)
        threshold_val = int(threshold * total_chars)
        chinese_count = 0

        # Convert chunk to UTF-32 and count codepoints in range
        mem_threshold = 50_000_000
        if total_chars <= mem_threshold:
            # If mem alloc < ~200MB, count in one go
            char_arr = np.frombuffer(text.encode("utf-32-le"), dtype="<u4")
            chinese_count = int(np.sum((char_arr >= 0x4E00) & (char_arr <= 0x9FFF)))
        else:
            # If mem alloc > ~200MB, count chunk by chunk, we will still outspeed regex or ord :-)
            for start in range(0, total_chars, mem_threshold):
                end = min(start + mem_threshold, total_chars)
                char_arr = np.frombuffer(text[start:end].encode("utf-32-le"), dtype="<u4")
                chinese_count += int(np.sum((char_arr >= 0x4E00) & (char_arr <= 0x9FFF)))
                # Early decision: already met threshold
                if chinese_count >= threshold_val:
                    return "zh"

        # Fallback: use punctuation types as heuristics
        is_chinese = int(chinese_count) >= threshold_val
        if is_chinese is False:
            using_chinese_puncs = text.count("？") > text.count("?") and text.count("！") > text.count("!")
            # using_chinese_puncs |= text.count("，") > text.count(",") or text.count("。") > text.count(".")
            is_chinese = using_chinese_puncs
        return "zh" if is_chinese else "en"

    def __call__(self, doc: str) -> List[Tuple[str, int, int]]:
        """
        Split document into sentence-level chunks

        Args:
            doc: Document text to be split

        Returns:
            List of chunks, each element is (text, start char position, end char position)
        """
        if not doc or not doc.strip():
            return []

        # Auto-detect language based on Chinese character ratio

        detected_lan = self.default_lan or self._detect_chinese(doc)
        self.seg = Segmenter(language=detected_lan, clean=False)

        sentences_with_spans = self._sentences_with_spans(doc)
        chunks: List[Tuple[str, int, int]] = []
        cur_sents: List[Tuple[str, int, int, int]] = []  # (text, start, end, token_len)

        for sent_text, sent_start, sent_end, sent_len in sentences_with_spans:
            if not sent_text.strip():
                continue

            if sent_len > self.chunk_size:
                chunks, cur_sents = self._flush(chunks, cur_sents)
                chunks.append((sent_text, sent_start, sent_end))
                continue

            cur_token_count = sum(s[3] for s in cur_sents)
            if cur_token_count + sent_len <= self.chunk_size:
                cur_sents.append((sent_text, sent_start, sent_end, sent_len))
            else:
                chunks, cur_sents = self._flush(chunks, cur_sents)
                cur_sents = [(sent_text, sent_start, sent_end, sent_len)]

        chunks, _ = self._flush(chunks, cur_sents)
        logger.info(f"Computed the following sentence-level chunks: {len(chunks)} chunks")
        return chunks

    def _sentences_with_spans(self, text: str) -> List[Tuple[str, int, int, int]]:
        sentences = self.seg.segment(text)
        used_spans = set()
        spans = []

        for sent in sentences:
            if not sent.strip():
                continue

            # Pre-calculate token length once
            sent_tokens = len(self.tokenizer_enc(sent))

            # Search for all occurrences
            idx = 0
            while True:
                idx = text.find(sent, idx)
                if idx == -1:
                    logger.warning(f"Span recovery failed for: {repr(sent[:30])}...")
                    break

                span = (idx, idx + len(sent))
                if span not in used_spans:
                    used_spans.add(span)
                    spans.append((sent, span[0], span[1], sent_tokens))
                    break

                idx += 1

        return spans

    def _flush(
        self, chunks: List[Tuple[str, int, int]], cur_sents: List[Tuple[str, int, int, int]]
    ) -> Tuple[List[Tuple[str, int, int]], List[Tuple[str, int, int, int]]]:
        if not cur_sents:
            return chunks, []

        chunk_text = "".join(s[0] for s in cur_sents)
        start_char = cur_sents[0][1]
        end_char = cur_sents[-1][2]
        chunks.append((chunk_text, start_char, end_char))

        # Handle overlap
        next_cur_sents = []
        if self.chunk_overlap > 0 and len(cur_sents) > 1:
            overlap_tokens = 0
            overlap_sents = []
            for sent_text, s_start, s_end, sent_toks in reversed(cur_sents):
                if overlap_tokens + sent_toks <= self.chunk_overlap:
                    overlap_sents.append((sent_text, s_start, s_end, sent_toks))
                    overlap_tokens += sent_toks
                else:
                    break
            next_cur_sents = list(reversed(overlap_sents))

        return chunks, next_cur_sents

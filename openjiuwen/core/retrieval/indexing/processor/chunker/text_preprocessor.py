# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Text preprocessing module for cleaning and normalizing text before indexing.
"""

import re
from abc import ABC, abstractmethod
from typing import List


class TextPreprocessor(ABC):
    """Base class for text preprocessors."""

    @abstractmethod
    def process(self, text: str) -> str:
        """
        Process the input text and return the cleaned version.

        Args:
            text: The input text to process

        Returns:
            The processed text
        """

    def __call__(self, text: str) -> str:
        """Allow preprocessor to be called as a function."""
        return self.process(text)


class WhitespaceNormalizer(TextPreprocessor):
    """
    Normalizes whitespace by replacing consecutive spaces, newlines,
    tabs, and other special characters with a single space.
    """

    def process(self, text: str) -> str:
        """
        Normalize whitespace in the text.

        Args:
            text: The input text

        Returns:
            Text with normalized whitespace
        """
        if not text:
            return text

        # Replace all whitespace sequences (including newlines) with single space
        text = re.sub(r"\s+", " ", text)

        # Remove leading and trailing whitespace
        text = text.strip()

        return text


class URLEmailRemover(TextPreprocessor):
    """
    Removes URLs and email addresses from text.
    """

    URL_PATTERN = re.compile(r"https?://\S+|www\.\S+")
    COM_PATTERN = re.compile(r"(?:https?://|www\.)?\S+?\.(?:com|net|org|cn)(?:[/?#]\S*)?\b")

    EMAIL_PATTERN = re.compile(r"\b[\w\.-]+@[\w\.-]+\.\w+\b")

    def __init__(self, remove_urls: bool = True, remove_emails: bool = True, replacement: str = ""):
        """
        Initialize the URL and email remover.

        Args:
            remove_urls: If True, removes URLs from text
            remove_emails: If True, removes email addresses from text
            replacement: String to replace URLs/emails with (default: empty string)
        """
        self.remove_urls = remove_urls
        self.remove_emails = remove_emails
        self.replacement = replacement

    def process(self, text: str) -> str:
        """
        Remove URLs and/or email addresses from text.

        Args:
            text: The input text

        Returns:
            Text with URLs and/or emails removed
        """
        if not text:
            return text

        if self.remove_urls:
            text = self.URL_PATTERN.sub(self.replacement, text)
            text = self.COM_PATTERN.sub(self.replacement, text)

        if self.remove_emails:
            text = self.EMAIL_PATTERN.sub(self.replacement, text)

        return text


class SpecialCharacterNormalizer(TextPreprocessor):
    """
    Normalizes or removes special characters from text.
    """

    CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x1F\x7F]")
    REDUNDANT_SYMBOLS_PATTERN = re.compile(
        r"(?![\u4e00-\u9fa5，。！？；：“”‘’（）【】《》、…·])" r"(?![#*\-_]{2,})" r"([^\w\s#*_\-\|]){2,}"
    )

    def __init__(self, chars_to_remove: str = "", chars_to_replace: dict = None):
        """
        Initialize the special character normalizer.

        Args:
            chars_to_remove: String containing characters to remove
            chars_to_replace: Dictionary mapping characters to their replacements
        """
        self.chars_to_remove = chars_to_remove
        self.chars_to_replace = chars_to_replace or {}

    def process(self, text: str) -> str:
        """
        Normalize special characters in text.

        Args:
            text: The input text

        Returns:
            Text with special characters normalized
        """
        if not text:
            return text

        # Remove control characters and redundant symbols
        text = self.CONTROL_CHAR_PATTERN.sub("", text)
        text = self.REDUNDANT_SYMBOLS_PATTERN.sub("", text)

        # Replace characters according to mapping
        for old_char, new_char in self.chars_to_replace.items():
            text = text.replace(old_char, new_char)

        # Remove specified characters
        if self.chars_to_remove:
            pattern = f"[{re.escape(self.chars_to_remove)}]"
            text = re.sub(pattern, "", text)

        return text


class PreprocessingPipeline:
    """
    Manages a pipeline of text preprocessors that are applied sequentially.
    """

    def __init__(self, preprocessors: List[TextPreprocessor] = None):
        """
        Initialize the preprocessing pipeline.

        Args:
            preprocessors: List of TextPreprocessor instances to apply in order
        """
        self.preprocessors = preprocessors or []

    def add_preprocessor(self, preprocessor: TextPreprocessor):
        """
        Add a preprocessor to the pipeline.

        Args:
            preprocessor: The preprocessor to add
        """
        self.preprocessors.append(preprocessor)

    def process(self, text: str) -> str:
        """
        Apply all preprocessors in the pipeline to the text.

        Args:
            text: The input text

        Returns:
            The fully processed text
        """
        for preprocessor in self.preprocessors:
            text = preprocessor.process(text)
        return text

    def __call__(self, text: str) -> str:
        """Allow pipeline to be called as a function."""
        return self.process(text)

    def __len__(self) -> int:
        """Return the number of preprocessors in the pipeline."""
        return len(self.preprocessors)

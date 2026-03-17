# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Callback Classes for Tracking Indexing Progress

Subclasses of BaseCallback can be passed into Indexer constructor to implement various form of progress tracking.
"""

import threading
import warnings
from typing import Optional, Sequence

import tqdm

warnings.filterwarnings("ignore", category=tqdm.TqdmExperimentalWarning)


class BaseCallback:
    """Base callback class for tracking embedding progress"""

    def __init__(self, seq: Sequence, **kwargs) -> None:
        self._call_counter = 0
        self._thread_lock = threading.Lock()

    def __call__(self, start_idx: int = -1, end_idx: int = -1, batch: Optional[list[str]] = None, **kwargs) -> None:
        """Empty callback function"""
        with self._thread_lock:
            self._call_counter += 1

    @property
    def call_counter(self) -> int:
        """Get call counter for current callback object"""
        return self._call_counter


class TqdmCallback(BaseCallback):
    """Tqdm callback class for tracking embedding progress"""

    def __init__(self, seq: Sequence, use_rich: bool = False, desc: str = "Indexing", **kwargs) -> None:
        super().__init__(seq=seq, **kwargs)
        if use_rich:
            from tqdm import rich

            tqdm_cls = rich.tqdm
        else:
            from tqdm import auto

            tqdm_cls = auto.tqdm
        self.__length = len(seq)
        self.__progress_bar = tqdm_cls(total=self.__length, desc=desc, **kwargs)

    def __call__(self, start_idx: int = -1, end_idx: int = -1, batch: Optional[list[str]] = None, **kwargs) -> None:
        """Increment tqdm progress bar by 1"""
        with self._thread_lock:
            self.__progress_bar.update()
            self._call_counter += 1
            if self._call_counter >= self.__length:
                self.__progress_bar.close()

    def __len__(self) -> int:
        return self.__length

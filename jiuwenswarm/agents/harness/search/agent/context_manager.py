"""
模块说明：
    多模态对话上下文管理器，用于计算和跟踪对话历史中的 token 用量。

主要功能：
    1. 基于 qwen3.5-27B tokenizer 计算文本内容的 token 数量
    2. 基于 base64 图片解析宽高，按 像素数 // 1024 计算图像 token 数量
    3. 对整体对话历史进行 token 总量统计
"""
import base64
import io
import logging
import re
from typing import Any, Dict, List, Optional

from PIL import Image
try:
    from transformers import AutoTokenizer
except ImportError:
    AutoTokenizer = None
# 全局 tokenizer 缓存，避免每次创建 ContextManager 实例时重复加载
_tokenizer_cache: Dict[str, Any] = {}


def _get_or_load_tokenizer(tokenizer_id: str, logger: logging.Logger) -> Optional[Any]:
    if tokenizer_id in _tokenizer_cache:
        logger.info(f"tokenizer 命中全局缓存: {tokenizer_id}")
        return _tokenizer_cache[tokenizer_id]

    if AutoTokenizer is None:
        logger.warning("transformers 未安装，将使用字符数近似估算文本 token")
        return None

    try:
        tokenizer = AutoTokenizer.from_pretrained(
            tokenizer_id, trust_remote_code=True
        )
        _tokenizer_cache[tokenizer_id] = tokenizer
        logger.info(f"tokenizer 加载成功并写入全局缓存: {tokenizer_id}")
        return tokenizer
    except Exception as e:
        logger.warning(
            f"tokenizer 加载失败 ({tokenizer_id}): {e}，"
            "将使用字符数近似估算文本 token"
        )
        # 缓存失败结果，避免后续每个 ContextManager 实例都重复尝试加载
        # （MMSearchAgent 每次 invoke 都新建 ContextManager，不缓存会反复加载）
        _tokenizer_cache[tokenizer_id] = None
        return None


def _get_image_dimensions_from_base64(b64_str: str) -> Optional[tuple]:
    raw = b64_str.strip()

    # 去除 data:image/xxx;base64, 前缀
    if raw.startswith("data:"):
        match = re.match(r"^data:image/[^;]+;base64,", raw)
        if match:
            raw = raw[match.end():]

    try:
        img_bytes = base64.b64decode(raw)
        img = Image.open(io.BytesIO(img_bytes))
        return img.size  # (width, height)
    except Exception:
        return None


def _calculate_image_tokens(width: int, height: int) -> int:
    pixels = width * height
    return max(pixels // 1024, 1)


class ContextManager:
    # qwen3.5-27B 对应的模型路径或名称
    DEFAULT_TOKENIZER_NAME = "/kos_ulan/yangzhihan/origin_model/qwen/Qwen3.5-27B"

    def __init__(
            self,
            max_context_tokens: int = 131072,
            compact_threshold: int = 0,
            tokenizer_name: Optional[str] = None,
            logger: Optional[logging.Logger] = None,
    ):
        self.max_context_tokens = max_context_tokens
        self.compact_threshold = compact_threshold
        self.total_tokens = 0
        self.logger = logger or logging.getLogger(self.__class__.__name__)

        # 通过全局缓存加载 tokenizer，同一 tokenizer_id 仅加载一次。
        # tokenizer_name=None → 不加载 AutoTokenizer，直接降级为字符估算，
        # 避免回退到 DEFAULT_TOKENIZER_NAME（本机通常不存在的路径）后每次
        # MMSearchAgent 构造都重复尝试加载（失败不缓存、可能触发网络解析）。
        tokenizer_id = tokenizer_name
        self.tokenizer = (
            _get_or_load_tokenizer(tokenizer_id, self.logger) if tokenizer_id else None
        )

    def count_text_tokens(self, text: str) -> int:
        if not text:
            return 0

        if self.tokenizer is not None:
            try:
                return len(self.tokenizer.encode(text))
            except Exception:
                # tokenizer 编码失败时降级为字符数近似
                pass

        # 降级方案：中文约 1 字符 ≈ 1 token，英文约 4 字符 ≈ 1 token
        # 取 len(text) * 0.75 作为近似值，向上取整
        return max(int(len(text) * 0.75), 1)

    def count_image_tokens(self, b64_str: str) -> int:
        dims = _get_image_dimensions_from_base64(b64_str)
        if dims is None:
            self.logger.warning("无法解析 base64 图片尺寸，使用默认 token 数 1024")
            return 1024

        width, height = dims
        tokens = _calculate_image_tokens(width, height)
        # self.logger.debug(f"图片尺寸 {width}x{height}，token 数: {tokens}")
        return tokens

    def count_message_tokens(self, message: Dict[str, Any]) -> int:
        total = 0
        content = message.get("content")

        if content is None:
            return 0

        # content 为纯文本字符串
        if isinstance(content, str):
            return self.count_text_tokens(content)

        # content 为多模态内容列表
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                part_type = part.get("type")

                if part_type == "text":
                    total += self.count_text_tokens(part.get("text", ""))
                elif part_type == "image_url":
                    # image_url 字段可能是字符串或 {"url": "..."} 字典
                    image_url_data = part.get("image_url", "")
                    if isinstance(image_url_data, dict):
                        url = image_url_data.get("url", "")
                    else:
                        url = str(image_url_data)

                    # 仅对 base64 格式的图片计算 token
                    if url and ("base64," in url or not url.startswith(("http://", "https://"))):
                        total += self.count_image_tokens(url)
                    else:
                        # 非 base64 的 HTTP URL 图片，使用默认估算值
                        total += 1024

        # 消息中 role/tool_call_id 等元数据的开销估算
        total += 4
        return total

    def count_conversation_tokens(self, messages: List[Dict[str, Any]]) -> int:
        total = 0
        for msg in messages:
            total += self.count_message_tokens(msg)
        self.total_tokens = total
        return total

    def is_context_exhausted(self, messages: List[Dict[str, Any]]) -> bool:
        return self.count_conversation_tokens(messages) > self.max_context_tokens

    def get_remaining_tokens(self, messages: List[Dict[str, Any]]) -> int:
        used = self.count_conversation_tokens(messages)
        return max(self.max_context_tokens - used, 0)

    def _group_rounds(self, messages: List[Dict[str, Any]]) -> List[List[int]]:
        rounds: List[List[int]] = []
        current_round: List[int] = []

        for idx, msg in enumerate(messages):
            role = msg.get("role")

            # assistant 消息且包含 tool_calls，标志新一轮开始
            if role == "assistant" and msg.get("tool_calls"):
                # 上一轮有内容则保存
                if current_round:
                    rounds.append(current_round)
                current_round = [idx]
            elif current_round and role == "tool":
                # 属于当前轮的 tool 响应
                current_round.append(idx)

        # 保存最后一轮
        if current_round:
            rounds.append(current_round)

        return rounds

    @staticmethod
    def _round_tool_calls_key(message: Dict[str, Any]) -> str:
        tool_calls = message.get("tool_calls") or []
        parts = []
        for tc in tool_calls:
            func = tc.get("function", {}) if isinstance(tc, dict) else {}
            name = func.get("name", "")
            arguments = func.get("arguments", "")
            parts.append(f"{name}({arguments})")
        return "||".join(parts)

    @staticmethod
    def _round_llm_output_key(message: Dict[str, Any]) -> str:
        content = message.get("content", "") or ""
        reasoning = message.get("reasoning_content", "") or ""
        if reasoning:
            content = content + reasoning
        tool_calls = message.get("tool_calls") or []
        tc_parts = []
        for tc in tool_calls:
            func = tc.get("function", {}) if isinstance(tc, dict) else {}
            name = func.get("name", "")
            arguments = func.get("arguments", "")
            tc_parts.append(f"{name}({arguments})")
        return f"{content}||{'||'.join(tc_parts)}"

    def detect_repeated_tool_calls(
            self,
            messages: List[Dict[str, Any]],
            n: int = 3,
    ) -> Dict[str, Any]:
        if n < 2 or len(messages) < 2:
            return {"detected": False, "repeat_start_round": -1,
                    "llm_output_fingerprint": "", "truncated_messages": None}

        rounds = self._group_rounds(messages)

        # 轮次不足 n 轮，不可能出现 n 轮重复
        if len(rounds) < n:
            return {"detected": False, "repeat_start_round": -1,
                    "llm_output_fingerprint": "", "truncated_messages": None}

        # 计算每轮的 LLM 输出指纹
        llm_output_keys: List[str] = []
        for round_indices in rounds:
            # 每轮的第一条是 assistant 消息
            assistant_idx = round_indices[0]
            assistant_msg = messages[assistant_idx]
            llm_output_keys.append(self._round_llm_output_key(assistant_msg))

        # 滑动窗口检测连续 n 轮 LLM 输出完全相同
        repeat_start = -1
        for i in range(len(llm_output_keys) - n + 1):
            llm_window = llm_output_keys[i: i + n]
            # 空指纹不参与重复判定
            if not llm_window[0]:
                continue
            if len(set(llm_window)) == 1:
                repeat_start = i
                break

        if repeat_start == -1:
            return {"detected": False, "repeat_start_round": -1,
                    "llm_output_fingerprint": "", "truncated_messages": None}

        fingerprint = llm_output_keys[repeat_start]
        self.logger.warning(
            f"检测到连续 {n} 轮 LLM 输出重复，"
            f"重复起始轮: 第 {repeat_start} 轮，"
            f"LLM 输出指纹: {fingerprint[:200]}"
        )

        # 构建截断后的消息列表：仅保留 system prompt + 第一条 user 消息
        system_msgs = [m for m in messages if m.get("role") == "system"]
        truncated = system_msgs + [messages[1]]

        self.logger.info(
            f"检测到重复，将截断对话历史为 {len(truncated)} 条消息"
            f"（{len(system_msgs)} 条 system + 首条 user）"
        )
        return {
            "detected": True,
            "repeat_start_round": repeat_start,
            "llm_output_fingerprint": fingerprint,
            "truncated_messages": truncated,
        }

    def check_and_compact(
            self,
            messages: List[Dict[str, Any]],
            repeat_threshold: int = 3,
            cur_loop_step: int = None,
    ) -> Dict[str, Any]:
        # 第一步：纯检测（不修改 messages）
        detection = self.detect_repeated_tool_calls(messages, n=repeat_threshold)
        repeated = detection["detected"]
        repeated_fingerprint = ""

        if repeated:
            # 显式应用截断：用截断后的消息列表替换原列表内容
            truncated = detection["truncated_messages"]
            repeated_fingerprint = detection["llm_output_fingerprint"]
            messages.clear()
            messages.extend(truncated)

        # 第二步：计算 token 总量（基于可能已被截断的历史）
        total_tokens = self.count_conversation_tokens(messages)
        remaining_tokens = max(self.max_context_tokens - total_tokens, 0)
        exhausted = total_tokens > self.max_context_tokens

        # 第三步：若 compact_threshold > 0 且 token 总量超限，截断对话历史
        # 保留：system + 首条 user（原始 query）+ 最近 KEEP_RECENT_ROUNDS 轮
        # （每轮 = assistant(tool_calls) + 紧随其后的 tool 响应），丢弃中间历史。
        # 比旧逻辑"只留 system+首条 user"温和：保留最近证据，避免重搜循环。
        KEEP_RECENT_ROUNDS = 2
        compacted = False
        if self.compact_threshold > 0 and total_tokens > self.compact_threshold:
            system_msgs = [m for m in messages if m.get("role") == "system"]
            user_msgs = [m for m in messages if m.get("role") == "user"]
            first_user = user_msgs[0] if user_msgs else None
            rounds = self._group_rounds(messages)  # list[list[int]]，按轮分组
            keep_idx: set[int] = set()
            for _r in rounds[-KEEP_RECENT_ROUNDS:]:
                keep_idx.update(_r)
            tail = [m for i, m in enumerate(messages) if i in keep_idx]
            truncated = system_msgs + ([first_user] if first_user else []) + tail
            # 保留末尾 user 消息（如最后一轮注入的强制终止提示 force_answer），
            # 避免被压缩丢弃导致 LLM 看不到"必须作答"指令。
            if messages and messages[-1].get("role") == "user" and messages[-1] not in truncated:
                truncated.append(messages[-1])
            self.logger.warning(
                f"上下文 token 超限截断 | 阈值: {self.compact_threshold} | "
                f"截断前: {total_tokens} tokens / {len(messages)} 条消息 | "
                f"截断后: {len(truncated)} 条消息"
                f"（{len(system_msgs)} system + 1 user + {len(tail)} 最近{KEEP_RECENT_ROUNDS}轮）"
            )
            messages.clear()
            messages.extend(truncated)
            compacted = True
            total_tokens = self.count_conversation_tokens(messages)
            remaining_tokens = max(self.max_context_tokens - total_tokens, 0)
            exhausted = total_tokens > self.max_context_tokens

        self.logger.info(
            f"上下文检查 | 重复截断: {repeated} | token超限截断: {compacted} | "
            f"token: {total_tokens}/{self.max_context_tokens} | "
            f"剩余: {remaining_tokens} | 超限: {exhausted} | 当前轮次：{cur_loop_step}"
        )

        return {
            "repeated": repeated,
            "repeated_fingerprint": repeated_fingerprint,
            "compacted": compacted,
            "total_tokens": total_tokens,
            "remaining_tokens": remaining_tokens,
            "exhausted": exhausted,
        }
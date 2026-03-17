# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import secrets

from Crypto.Cipher import AES

NONCE_LENGTH = 12
BIT_LENGTH = 8
AES_KEY_LENGTH = 32
TAG_LENGTH = 16


def encrypt(key: bytes, plaintext: str):
    if len(key) != AES_KEY_LENGTH:
        raise ValueError(f'Wrong key length: {len(key)}, expected {AES_KEY_LENGTH}')
    random_instance = secrets.SystemRandom()
    nonce = bytes([random_instance.getrandbits(BIT_LENGTH) for _ in range(0, NONCE_LENGTH)])
    cipher = AES.new(key=key, mode=AES.MODE_GCM, nonce=nonce, mac_len=TAG_LENGTH)
    cipher_text, tag = cipher.encrypt_and_digest(plaintext.encode(encoding="utf-8"))
    return [cipher_text.hex(), nonce.hex(), tag.hex()]


def decrypt(key: bytes, ciphertext: str, nonce: str, tag: str):
    ciphertext_bytes = bytes.fromhex(ciphertext)
    nonce_bytes = bytes.fromhex(nonce)
    tag_bytes = bytes.fromhex(tag)
    if len(key) != AES_KEY_LENGTH:
        raise ValueError(f'Wrong key length: {len(key)}, expected {AES_KEY_LENGTH}')

    if len(nonce_bytes) != NONCE_LENGTH:
        raise ValueError(f"Wrong nonce length: {len(nonce_bytes)}")

    if len(tag_bytes) != TAG_LENGTH:
        raise ValueError(f"Wrong tag length: {len(tag_bytes)}, expected {TAG_LENGTH}")

    cipher = AES.new(key=key, mode=AES.MODE_GCM, nonce=nonce_bytes)
    plaintext_bytes = cipher.decrypt_and_verify(ciphertext=ciphertext_bytes, received_mac_tag=tag_bytes)
    return plaintext_bytes.decode(encoding="utf-8")

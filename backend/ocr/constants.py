# backend/ocr/constants.py

from enum import StrEnum  # Python 3.11+

class ReceiptState(StrEnum):
    COLLECTED = "collected"
    OCR_DONE = "ocr_done"
    VECTORIZED = "vectorized"
    BRAND_STORE_IDENTIFIED = "brand_store_identified"

import pytest
import numpy as np
from PIL import Image
import os
import io

from app.services import vision
from app.config import settings

def test_vision_pipeline_high_confidence_indomain(monkeypatch):
    """Test high confidence, stable TTA, in-distribution -> Confident."""
    def mock_predict_array(model, x, tta=True):
        return {"probs": np.array([0.0, 0.0, 0.95, 0.0, 0.05]), "agreement": 0.99}
    
    def mock_ood(model, x):
        return 0.5
    
    monkeypatch.setattr(vision, "predict_array", mock_predict_array)
    monkeypatch.setattr(vision, "_ood_distance", mock_ood)
    
    # Just pass dummy bytes, we mock the core functions
    img = Image.new("RGB", (96, 96))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data = buf.getvalue()
    
    res = vision.predict_bytes(data)
    assert res["verdict"] == "Confident"
    assert res["out_of_distribution"] is False

def test_vision_pipeline_low_confidence(monkeypatch):
    """Test low confidence -> Uncertain."""
    def mock_predict_array(model, x, tta=True):
        return {"probs": np.array([0.4, 0.4, 0.1, 0.1, 0.0]), "agreement": 0.99}
    
    def mock_ood(model, x):
        return 0.5
    
    monkeypatch.setattr(vision, "predict_array", mock_predict_array)
    monkeypatch.setattr(vision, "_ood_distance", mock_ood)
    
    img = Image.new("RGB", (96, 96))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data = buf.getvalue()
    
    res = vision.predict_bytes(data)
    assert res["verdict"] == "Uncertain"
    assert "low confidence" in res["uncertainty_reason"]

def test_vision_pipeline_unstable_tta(monkeypatch):
    """Test unstable TTA -> Uncertain."""
    def mock_predict_array(model, x, tta=True):
        return {"probs": np.array([0.0, 0.0, 0.99, 0.0, 0.01]), "agreement": 0.70}
    
    def mock_ood(model, x):
        return 0.5
    
    monkeypatch.setattr(vision, "predict_array", mock_predict_array)
    monkeypatch.setattr(vision, "_ood_distance", mock_ood)
    
    img = Image.new("RGB", (96, 96))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data = buf.getvalue()
    
    res = vision.predict_bytes(data)
    assert res["verdict"] == "Uncertain"
    assert "low flip-TTA agreement" in res["uncertainty_reason"]

def test_vision_pipeline_ood(monkeypatch):
    """Test high confidence, stable TTA, OOD -> OOD / Do Not Trust."""
    def mock_predict_array(model, x, tta=True):
        return {"probs": np.array([0.0, 0.0, 0.99, 0.0, 0.01]), "agreement": 0.99}
    
    def mock_ood(model, x):
        return 4.02 # High OOD distance
    
    monkeypatch.setattr(vision, "predict_array", mock_predict_array)
    monkeypatch.setattr(vision, "_ood_distance", mock_ood)
    
    img = Image.new("RGB", (96, 96))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data = buf.getvalue()
    
    res = vision.predict_bytes(data)
    assert res["verdict"] == "OOD / Do Not Trust"
    assert res["out_of_distribution"] is True
    assert "visually different from the training data" in res["uncertainty_reason"]

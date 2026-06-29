from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gpu_config import (
    SUPPORTED_DEVICES,
    SUPPORTED_COMPUTE_TYPES,
    clean_device,
    clean_compute_type,
    device_status,
    gpu_name,
    has_nvidia_gpu,
    resolve_device_and_compute,
)


class TestHasNvidiaGpu(unittest.TestCase):
    def test_nvidia_smi_succeeds_returns_true(self):
        with patch("gpu_config.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "GPU 0: NVIDIA GeForce RTX 4090\n"
            self.assertTrue(has_nvidia_gpu())

    def test_nvidia_smi_fails_returns_false(self):
        with patch("gpu_config.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = ""
            self.assertFalse(has_nvidia_gpu())

    def test_exception_returns_false(self):
        with patch("gpu_config.subprocess.run", side_effect=FileNotFoundError):
            self.assertFalse(has_nvidia_gpu())


class TestGpuName(unittest.TestCase):
    def test_no_gpu_returns_empty(self):
        with patch("gpu_config.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            self.assertEqual(gpu_name(), "")

    def test_returns_name(self):
        with patch("gpu_config.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "NVIDIA GeForce RTX 4090\n"
            self.assertEqual(gpu_name(), "NVIDIA GeForce RTX 4090")

    def test_exception_returns_empty(self):
        with patch("gpu_config.subprocess.run", side_effect=FileNotFoundError):
            self.assertEqual(gpu_name(), "")


class TestCleanDevice(unittest.TestCase):
    def test_returns_valid_device(self):
        for d in SUPPORTED_DEVICES:
            self.assertEqual(clean_device(d), d)

    def test_falls_back_to_auto_for_unknown(self):
        self.assertEqual(clean_device("rocm"), "auto")
        self.assertEqual(clean_device(""), "auto")
        self.assertEqual(clean_device(None), "auto")

    def test_case_insensitive(self):
        self.assertEqual(clean_device("CUDA"), "cuda")
        self.assertEqual(clean_device("CPU"), "cpu")

    def test_strips_whitespace(self):
        self.assertEqual(clean_device("  cuda  "), "cuda")


class TestCleanComputeType(unittest.TestCase):
    def test_returns_valid_compute_type(self):
        for c in SUPPORTED_COMPUTE_TYPES:
            self.assertEqual(clean_compute_type(c), c)

    def test_falls_back_to_auto_for_unknown(self):
        self.assertEqual(clean_compute_type("bfloat16"), "auto")
        self.assertEqual(clean_compute_type(""), "auto")
        self.assertEqual(clean_compute_type(None), "auto")

    def test_case_insensitive(self):
        self.assertEqual(clean_compute_type("FLOAT16"), "float16")

    def test_strips_whitespace(self):
        self.assertEqual(clean_compute_type("  int8  "), "int8")


class TestResolveDeviceAndCompute(unittest.TestCase):
    def test_auto_with_gpu_returns_cuda_float16(self):
        with patch("gpu_config.has_nvidia_gpu", return_value=True):
            device, compute = resolve_device_and_compute("auto", "auto")
            self.assertEqual(device, "cuda")
            self.assertEqual(compute, "float16")

    def test_auto_without_gpu_returns_cpu_int8(self):
        with patch("gpu_config.has_nvidia_gpu", return_value=False):
            device, compute = resolve_device_and_compute("auto", "auto")
            self.assertEqual(device, "cpu")
            self.assertEqual(compute, "int8")

    def test_explicit_device_preserved(self):
        with patch("gpu_config.has_nvidia_gpu", return_value=True):
            device, compute = resolve_device_and_compute("cpu", "auto")
            self.assertEqual(device, "cpu")
            self.assertEqual(compute, "int8")

    def test_explicit_compute_type_preserved(self):
        with patch("gpu_config.has_nvidia_gpu", return_value=True):
            device, compute = resolve_device_and_compute("auto", "float32")
            self.assertEqual(device, "cuda")
            self.assertEqual(compute, "float32")

    def test_unknown_device_cleaned_to_auto_then_resolved(self):
        with patch("gpu_config.has_nvidia_gpu", return_value=False):
            device, compute = resolve_device_and_compute("rocm", "auto")
            self.assertEqual(device, "cpu")
            self.assertEqual(compute, "int8")

    def test_none_values_default_to_auto(self):
        with patch("gpu_config.has_nvidia_gpu", return_value=False):
            device, compute = resolve_device_and_compute(None, None)
            self.assertEqual(device, "cpu")


class TestDeviceStatus(unittest.TestCase):
    def test_returns_expected_keys(self):
        with patch("gpu_config.has_nvidia_gpu", return_value=False):
            with patch("gpu_config.gpu_name", return_value=""):
                status = device_status()
                self.assertIn("has_nvidia_gpu", status)
                self.assertIn("gpu_name", status)
                self.assertIn("auto_device", status)
                self.assertIn("auto_compute_type", status)

    def test_reports_gpu_when_available(self):
        with patch("gpu_config.has_nvidia_gpu", return_value=True):
            with patch("gpu_config.gpu_name", return_value="NVIDIA RTX 4090"):
                status = device_status()
                self.assertEqual(status["has_nvidia_gpu"], "true")
                self.assertEqual(status["gpu_name"], "NVIDIA RTX 4090")
                self.assertEqual(status["auto_device"], "cuda")

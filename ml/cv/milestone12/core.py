"""CUDA-only ResNet18, preprocessing, and binary evaluation primitives."""

import ctypes
from ctypes import wintypes
import random
import time

import numpy as np
import PIL
import torch
from torch import nn
import torchvision

from . import data


def seed_everything(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(True)
    torch.backends.cudnn.benchmark = False
    torch.backends.cuda.matmul.fp32_precision = "ieee"
    torch.backends.cudnn.conv.fp32_precision = "ieee"
    torch.set_num_threads(4)


def cuda_smoke():
    if not torch.cuda.is_available():
        raise RuntimeError("PyTorch CUDA is unavailable. Training requires a successful CUDA smoke test.")
    device = torch.device("cuda:0")
    matrix = torch.tensor([[1.0, 2.0], [3.0, 4.0]], device=device, requires_grad=True)
    product = matrix @ matrix
    product.sum().backward()
    convolution = nn.Conv2d(3, 4, 3).to(device)
    convolution(torch.ones(2, 3, 16, 16, device=device)).square().mean().backward()
    torch.cuda.synchronize(device)
    if (
        product.detach().cpu().tolist() != [[7.0, 10.0], [15.0, 22.0]]
        or not torch.isfinite(matrix.grad).all().item()
        or not torch.isfinite(convolution.weight.grad).all().item()
    ):
        raise RuntimeError("CUDA smoke produced incorrect or nonfinite results")
    free, total = torch.cuda.mem_get_info(device)
    return device, {
        "device": torch.cuda.get_device_name(device),
        "compute_capability": list(torch.cuda.get_device_capability(device)),
        "cuda_runtime": torch.version.cuda,
        "cudnn_version": torch.backends.cudnn.version(),
        "torch": torch.__version__,
        "torchvision": torchvision.__version__,
        "numpy": np.__version__,
        "pillow": PIL.__version__,
        "free_vram_bytes_at_smoke": free,
        "total_vram_bytes": total,
        "operations": ["CUDA matrix multiply and backward", "CUDA convolution and backward"],
    }


def architecture():
    return torchvision.models.resnet18(weights=None, num_classes=2)


def from_cached_weights(path, expected_hash, device):
    if data.digest(path) != expected_hash:
        raise ValueError("Cached ResNet18 weight file SHA-256 mismatch")
    network = torchvision.models.resnet18(weights=None)
    network.load_state_dict(torch.load(path, map_location="cpu", weights_only=True), strict=True)
    network.fc = nn.Linear(network.fc.in_features, 2)
    return network.to(device)


def set_stage(network, stage):
    if stage not in {"head", "fine_tune"}:
        raise ValueError(f"Unknown training stage {stage}")
    for name, parameter in network.named_parameters():
        parameter.requires_grad_(name.startswith("fc.") or (stage == "fine_tune" and name.startswith("layer4.")))
    network.eval()
    network.fc.train()
    if stage == "fine_tune":
        network.layer4.train()
        for module in network.layer4.modules():
            if isinstance(module, nn.BatchNorm2d):
                module.eval()
    if any(module.training for module in network.modules() if isinstance(module, nn.BatchNorm2d)):
        raise RuntimeError("BatchNorm running statistics must stay frozen")


def make_optimizer(network, config, stage):
    set_stage(network, stage)
    if stage == "head":
        groups = [{"params": network.fc.parameters(), "lr": config["head_lr"]}]
    else:
        groups = [
            {"params": network.layer4.parameters(), "lr": config["fine_tune_layer4_lr"]},
            {"params": network.fc.parameters(), "lr": config["fine_tune_head_lr"]},
        ]
    return torch.optim.AdamW(groups, weight_decay=config["weight_decay"], foreach=False)


def normalization(config, device):
    return tuple(torch.tensor(config[field], dtype=torch.float32, device=device).view(1, 3, 1, 1) for field in ("mean", "std"))


def tensorize(images, device, stats):
    mean, std = stats
    return torch.from_numpy(images).permute(0, 3, 1, 2).contiguous().to(device, dtype=torch.float32).div_(255).sub_(mean).div_(std)


def gpu_memory(device):
    return {
        "peak_vram_allocated_bytes": torch.cuda.max_memory_allocated(device),
        "peak_vram_reserved_bytes": torch.cuda.max_memory_reserved(device),
    }


def host_peak_bytes():
    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("page_faults", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in (
                "peak_working_set", "working_set", "peak_paged_pool", "paged_pool",
                "peak_nonpaged_pool", "nonpaged_pool", "pagefile", "peak_pagefile",
            )
        ]

    stats = ProcessMemoryCounters()
    stats.cb = ctypes.sizeof(stats)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCounters), wintypes.DWORD)
    result = psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(stats), stats.cb)
    return int(stats.peak_working_set) if result else None


def batch_probe(network, rows, config, workers, device, batch_size):
    # Full fine-tuning memory probe on training images. The scratch model is discarded.
    optimizer = make_optimizer(network, config, "fine_tune")
    images, targets = next(data.batches(rows[:batch_size], batch_size, config, workers))
    inputs = tensorize(images, device, normalization(config, device))
    loss = nn.functional.cross_entropy(network(inputs), torch.from_numpy(targets).to(device))
    loss.backward()
    optimizer.step()
    torch.cuda.synchronize(device)
    if not torch.isfinite(loss).item():
        raise ValueError("Nonfinite training-batch smoke loss")
    return gpu_memory(device)


def train_epoch(network, optimizer, rows, batch_size, config, workers, device, epoch, stage):
    set_stage(network, stage)
    stats = normalization(config, device)
    before = time.perf_counter()
    total, correct = torch.zeros((), device=device), torch.zeros((), device=device, dtype=torch.int64)
    for step, (images, targets) in enumerate(data.batches(rows, batch_size, config, workers, epoch=epoch), 1):
        texts = tensorize(images, device, stats)
        gold = torch.from_numpy(targets).to(device)
        optimizer.zero_grad(set_to_none=True)
        logits = network(texts)
        loss = nn.functional.cross_entropy(logits, gold)
        loss.backward()
        optimizer.step()
        total += loss.detach() * len(targets)
        correct += (logits.detach().argmax(dim=1) == gold).sum()
        if step % 250 == 0:
            print(f"Epoch {epoch} {stage}: {min(step * batch_size, len(rows)):,}/{len(rows):,} training images", flush=True)
    mean_loss = total.item() / len(rows)
    if not np.isfinite(mean_loss):
        raise ValueError(f"Nonfinite training loss in epoch {epoch}")
    return {"loss": mean_loss, "accuracy": correct.item() / len(rows), "support": len(rows),
            "seconds": time.perf_counter() - before}


@torch.inference_mode()
def score_rows(network, rows, batch_size, config, workers, device, *, measure=False):
    network.eval()
    stats = normalization(config, device)
    actual, scores, timings = [], [], []
    start = time.perf_counter()
    total_loss = torch.zeros((), device=device)
    for images, targets in data.batches(rows, batch_size, config, workers):
        inputs = tensorize(images, device, stats)
        if measure:
            begin, end = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
            begin.record()
        logits = network(inputs)
        if measure:
            end.record()
        truth = torch.from_numpy(targets).to(device)
        total_loss += nn.functional.cross_entropy(logits, truth, reduction="sum")
        scores.append(logits.softmax(dim=1)[:, 1].cpu().numpy())
        actual.append(targets)
        if measure:
            timings.append(begin.elapsed_time(end))
    scores = np.concatenate(scores)
    loss = total_loss.item() / len(rows)
    if not np.isfinite(scores).all() or not np.isfinite(loss):
        raise ValueError("Nonfinite evaluation output")
    run = {"loss": loss, "seconds": time.perf_counter() - start}
    if measure:
        run["timing"] = {
            "kind": "CUDA forward only, batched, first batch included",
            "batches": len(timings), "batch_size_limit": batch_size,
            "median_ms_per_batch": float(np.median(timings)),
            "mean_ms_per_image_amortized": sum(timings) / len(rows),
        }
    return np.concatenate(actual), scores, run


def binary_metrics(truth, crack_scores, threshold, label_order):
    guesses = (crack_scores >= threshold).astype(np.int64)
    matrix = np.bincount(2 * truth + guesses, minlength=4).reshape(2, 2)
    classes = {}
    for i, label in enumerate(label_order):
        correct = int(matrix[i, i])
        predicted, actual = int(matrix[:, i].sum()), int(matrix[i].sum())
        precision = correct / predicted if predicted else 0.0
        recall = correct / actual if actual else 0.0
        classes[label] = {"precision": precision, "recall": recall,
                          "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
                          "support": actual}
    return {
        "threshold": threshold, "accuracy": float(np.trace(matrix)) / len(truth),
        "macro_f1": sum(row["f1"] for row in classes.values()) / 2,
        "weighted_f1": sum(row["f1"] * row["support"] for row in classes.values()) / len(truth),
        "label_order": label_order, "per_class": classes,
        "confusion_matrix_actual_rows_predicted_columns": matrix.tolist(),
        "false_positives": int(matrix[0, 1]), "false_negatives": int(matrix[1, 0]),
        "support": len(truth),
    }


def threshold_search(truth, crack_scores, config):
    grid = config["threshold_grid"]
    trials = [
        binary_metrics(truth, crack_scores, p / 100, config["label_order"])
        for p in range(grid["start_percent"], grid["end_percent"] + 1, grid["step_percent"])
    ]
    selected = max(trials, key=lambda r: (r["macro_f1"], -abs(round(r["threshold"] * 100) - 50), -r["threshold"]))
    return selected, trials

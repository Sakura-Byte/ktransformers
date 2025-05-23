"""This file is used for /tests and /benchmarks"""
import numpy
import torch

SUPPORTED_NUM_BITS = [4, 8]
SUPPORTED_GROUP_SIZES = [-1, 32, 64, 128]


def get_pack_factor(num_bits):
    assert num_bits in SUPPORTED_NUM_BITS, f"Unsupported num_bits = {num_bits}"
    return 32 // num_bits


def permute_rows(q_w: torch.Tensor, w_ref: torch.Tensor, group_size: int):
    assert q_w.shape == w_ref.shape

    orig_device = q_w.device
    k_size, _ = q_w.shape

    g_idx = torch.zeros((k_size, ), dtype=torch.int32)
    for i in range(k_size):
        g_idx[i] = i // group_size

    # Simulate act_order by doing a random permutation on K
    rand_perm = torch.randperm(k_size)

    g_idx = g_idx[rand_perm].contiguous()
    q_w = q_w[rand_perm, :].contiguous()
    w_ref = w_ref[rand_perm, :].contiguous()

    return (
        w_ref.to(device=orig_device),
        q_w.to(device=orig_device),
        g_idx.to(device=orig_device),
        rand_perm.to(device=orig_device),
    )


# Function: Dequantize quantized weights
def dequantize_weights(qweight, qzeros, scales, g_idx, bits=4, group_size=128, device='cuda:0'):
    # Create a tensor for bitwise right shift operation
    wf = torch.tensor(list(range(0, 32, bits)), dtype=torch.int32, device=device).unsqueeze(0)

    # Apply bitwise right shift and convert qzeros to the appropriate type
    zeros = torch.bitwise_right_shift(torch.unsqueeze(qzeros, 2).expand(-1, -1, 32 // bits), wf.unsqueeze(0)).to(torch.int16 if bits == 8 else torch.int8)
    torch.bitwise_and(zeros, (2 ** bits) - 1, out=zeros)

    # Reshape the zeros tensor
    zeros = zeros + 1
    zeros = zeros.reshape(-1, 1, zeros.shape[1] * zeros.shape[2])

    # Reshape the scales tensor
    scales = scales.reshape(-1, 1, scales.shape[-1])

    # Similar bitwise right shift operation for qweight and reshape
    weight = torch.bitwise_right_shift(torch.unsqueeze(qweight, 1).expand(-1, 32 // bits, -1), wf.unsqueeze(-1)).to(torch.int16 if bits == 8 else torch.int8)
    torch.bitwise_and(weight, (2 ** bits) - 1, out=weight)
    weight = weight.reshape(-1, group_size, weight.shape[2])

    # Apply dequantization formula and reshape the final weight
    weight = (scales * (weight - zeros))
    weight = weight.reshape(weight.shape[0] * weight.shape[1], weight.shape[2])

    # Return the transposed weight
    return weight.transpose(0, 1)

def quantize_weights(w: torch.Tensor, num_bits: int, group_size: int,
                     act_order: bool):
    orig_device = w.device
    size_k, size_n = w.shape

    assert w.is_floating_point(), "w must be float"
    assert num_bits in SUPPORTED_NUM_BITS, f"Unsupported num_bits = {num_bits}"
    assert group_size in SUPPORTED_GROUP_SIZES + [
        size_k
    ], f"Unsupported groupsize = {group_size}"

    if group_size == -1:
        group_size = size_k
    assert group_size <= size_k

    max_q_val = 2**num_bits - 1
    half_q_val = (max_q_val + 1) // 2

    # Reshape to [groupsize, -1]
    if group_size < size_k:
        w = w.view((-1, group_size, size_n))
        w = w.permute(1, 0, 2)
        w = w.reshape((group_size, -1))

    # Compute scale for each group
    s = torch.max(torch.abs(w), 0, keepdim=True)[0]
    s *= 2 / max_q_val  # 2 => symmetric

    # Quantize
    q_w = torch.round(w / s).int()
    q_w += half_q_val
    q_w = torch.clamp(q_w, 0, max_q_val)

    # Compute ref (dequantized)
    w_ref = (q_w - half_q_val).half() * s

    # Restore original shapes
    if group_size < size_k:

        def reshape_w(w):
            w = w.reshape((group_size, -1, size_n))
            w = w.permute(1, 0, 2)
            w = w.reshape((size_k, size_n)).contiguous()
            return w

        q_w = reshape_w(q_w)
        w_ref = reshape_w(w_ref)

    s = s.reshape((-1, size_n)).contiguous()

    # Apply act_order
    g_idx = torch.empty(0, dtype=torch.int, device=w.device)
    rand_perm = torch.empty(0, dtype=torch.int, device=w.device)
    if act_order:
        assert (
            group_size < size_k
        ), "For act_order, groupsize = {} must be less than size_k = {}".format(
            group_size, size_k)

        w_ref, q_w, g_idx, rand_perm = permute_rows(q_w, w_ref, group_size)

    return (
        w_ref.to(device=orig_device),
        q_w.to(device=orig_device),
        s.to(device=orig_device),
        g_idx.to(device=orig_device),
        rand_perm.to(device=orig_device),
    )


def sort_weights(q_w: torch.Tensor, g_idx: torch.Tensor):
    orig_device = q_w.device

    sort_indices = torch.argsort(g_idx).to(
        dtype=torch.int32)  # Sort based on g_idx

    g_idx = g_idx[sort_indices].contiguous()
    q_w = q_w[sort_indices, :].contiguous()

    return (
        q_w.to(device=orig_device),
        g_idx.to(device=orig_device),
        sort_indices.to(device=orig_device),
    )


def gptq_pack(
    q_w: torch.Tensor,
    num_bits: int,
    size_k: int,
    size_n: int,
):
    assert q_w.shape == (size_k, size_n)

    pack_factor = get_pack_factor(num_bits)
    assert size_k % pack_factor == 0

    orig_device = q_w.device

    q_w = q_w.cpu().numpy().astype(numpy.uint32)

    q_res = numpy.zeros((size_k // pack_factor, size_n), dtype=numpy.uint32)

    for i in range(pack_factor):
        q_res |= q_w[i::pack_factor, :] << num_bits * i

    q_res = torch.from_numpy(q_res.astype(numpy.int32)).to(orig_device)
    return q_res

def gptq_unpack(
    q_res: torch.Tensor,
    num_bits: int,
    size_k: int,
    size_n: int,
):
    pack_factor = 32 // num_bits
    assert size_k % pack_factor == 0

    orig_device = q_res.device

    q_res = q_res.cpu().numpy()

    q_w = numpy.zeros((size_k, size_n), dtype=numpy.uint32)

    for i in range(pack_factor):
        q_w[i::pack_factor, :] = (q_res >> (num_bits * i)) & ((1 << num_bits) - 1)

    q_w = torch.from_numpy(q_w.astype(numpy.int32)).to(orig_device)
    return q_w
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torch.autograd import Variable
from torchvision import datasets, transforms
from tensorboardX import SummaryWriter

torch.manual_seed(4242)

data_dir = "./data/"
train_loader = torch.utils.data.DataLoader(
    datasets.MNIST(data_dir, train=True, download=True,
                   transform=transforms.Compose([
                       transforms.ToTensor(),
                       transforms.Normalize((0.1307,), (0.30801,))
                   ])),
    batch_size=64, shuffle=True)


def squash(input):
    """
    Squashing function for a tensor.
    :param input: torch.Tensor
    """
    assert (input.norm() > 0), "Division by zero in second term of equation"
    norm = input.norm()
    squared_norm = norm.pow(2)
    return (squared_norm/(1+squared_norm))*(input/norm)


conv1_params = {
    "in_channels": 1,
    "out_channels": 256,
    "kernel_size": 9,
    "stride": 1
}

conv2_params = {
    "in_channels": 256,
    "out_channels": 8,
    "kernel_size": 9,
    "stride": 2
}


class PrimaryCapsules(nn.Module):
    """
    Primary Capsule Network on MNIST.
    :param conv1_params: Parameters for first Conv2d layer
    :param conv2_params: Parameters for second Conv2d layer
    :param caps_maps: number of feature maps (capsules)
    :param caps_dims: dimension of each capsule's activation vector
    Shape:
        - Input: (batch, channels, height, width)
        - Output: (batch, n_caps, caps_dims)
    """
    def __init__(self, conv_params, caps_maps=32, caps_dims=8):
        super(PrimaryCapsules, self).__init__()
        self.caps_maps = caps_maps
        # Output of conv2 has 256 (32*8) maps of 6x6.
        # We instead want 32 vectors of 8 dims each.
        self.n_caps = caps_maps * 6 * 6
        self.caps_dims = caps_dims

        self.capsules = nn.ModuleList([
            nn.Conv2d(**conv_params) for _ in range(self.caps_maps)
        ])

    def forward(self, x):
        output = [capsule(x) for capsule in self.capsules]
        output = torch.cat(output)
        output = output.view(x.size(0), -1, self.caps_dims)
        return squash(output)

# https://github.com/laubonghaudoi/CapsNet_guide_PyTorch/blob/master/DigitCaps.py
class DigitCapsule(nn.Module):
    """
    Digit Capsule Layer.
    :param num_lower_capsules: Number of lower level capsules, used to calculate dynamic routing.
    Shape:
        - Input: (batch, channels, height, width)
        - Output: (batch, n_caps, caps_dims)
    """
    def __init__(self, num_route_nodes, in_channels, out_channels, num_iterations):
        super(DigitCapsule, self).__init__()
        self.num_digits = 10
        self.num_route_nodes = num_route_nodes
        self.num_iterations = num_iterations
        # W.shape => [1, 10, 1152, 8, 16]
        # (1) is to be broadcastable with torch.matmul
        self.W = nn.Parameter(torch.randn(1,
                                        self.num_route_nodes,
                                        self.num_digits,
                                        out_channels,
                                        in_channels,
                                        ))

    def forward(self, u):
        u = u[:,:,None,:,None,]
        u_hat = torch.matmul(self.W, u)
        u_hat = torch.squeeze(u_hat)

        # Routing Algorithm.
        # for all capsule i in layer l and capsule j in layer (l + 1): b_ij ← 0
        b = Variable(torch.zeros(self.num_route_nodes, self.num_digits))
        # for r iterations do
        for i in range(self.num_iterations):
            # for all capsule i in layer l: c_i ← softmax(b_i)
            c = F.softmax(b, dim=1).unsqueeze(2).unsqueeze(0)
            # for all capsule j in layer (l+1): s_j ← SUM_i ( c_ij * u_hat_j|i )
            s = torch.sum(u_hat * c, dim=1)
            # for all capsule j in layer (l + 1): v_j ← squash(s_j)
            v = squash(s)
            # for all capsule i in layer l and capsule j in layer (l + 1): b_ij ← b_ij + u_hat_j|i * v_j
            a = torch.matmul(u_hat.transpose(1, 2), v.unsqueeze(3))
            b = b + torch.sum(a.squeeze().transpose(1, 2), dim=0)
        return v

class CapsNet(nn.Module):
    def __init__(self, conv1_params, conv2_params):
        super(CapsNet, self).__init__()
        self.conv = nn.Conv2d(**conv1_params)
        self.primary_capsules = PrimaryCapsules(conv2_params, caps_dims=8)
        self.digit_capsules = DigitCapsule(num_route_nodes=32*6*6, in_channels=8, out_channels=16, num_iterations=3)

    def forward(self, x):
        print(f"CapsNet input size", x.size())
        x = F.relu(self.conv(x))
        print(f"CapsNet conv1 size", x.size())
        u = self.primary_capsules(x)
        print(f"CapsNet PrimaryCaps size", u.size())
        v = self.digit_capsules(u)
        print(f"CapsNet DigitCaps size", v.size())
        return v

def loss(v, target, batch_size):
    l = 0.5
    m = 0.9
    T = target.type(torch.FloatTensor)
    norm = torch.norm(v)
    zeros = Variable(torch.zeros(norm.size()))
    # L_k = T_k max(0, m^+ − ||v_k||)^2 + λ (1 − T_k) max(0, ||v_k|| − m^−)^2
    L = T * torch.max(zeros, m - norm) ** 2 + l * (1 -T) * torch.max(zeros, norm - (1. - m)) ** 2
    return torch.sum(L) / batch_size

# NOTE. What parameters would we like to experiment with?
# num of capsules in PrimaryCaps? Capsule Dimensions? Conv params?
model = CapsNet(conv1_params, conv2_params)

def train(model, epochs=100):
    optimizer = torch.optim.Adam(model.parameters(), lr=0.01)

    model.train()
    writer = SummaryWriter()
    for epoch in range(epochs):
        for batch_idx, (data, target) in enumerate(train_loader):
            test_sample = data
            batch_size = test_sample.size()[0]
            print(f"Sample size: {test_sample.size()}")
            output = model(data)
            L = loss(output, target, batch_size)
            L.backward()

            step = batch_idx + epoch
            writer.add_scalar('train/loss', L.data[0], step)

            optimizer.step()


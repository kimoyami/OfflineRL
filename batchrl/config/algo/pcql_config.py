import torch
from batchrl.utils.exp import select_free_cuda

task = "walker2d-medium-v0"
dataset_dir = "/home/revive/syg/datasets/walker2d/walker2d"
device = 'cuda'+":"+str(select_free_cuda()) if torch.cuda.is_available() else 'cpu'
obs_shape = None
act_shape = None


max_epoch = 200
steps_per_epoch = 1000
policy_bc_steps = 40000

vae_iterations = 500000
vae_hidden_size = 750
vae_batch_size = 100
vae_lr = 1e-4

lmbda = 0.75

batch_size = 256
hidden_layer_size = 256
layer_num = 3
actor_lr=1E-4
critic_lr=3E-4
reward_scale=1
use_automatic_entropy_tuning=True
target_entropy = None
discount = 0.99
soft_target_tau=5e-3

# min Q
explore=1.0
temp=1.0
min_q_version=3
min_q_weight=1.0

# lagrange
with_lagrange=False
lagrange_thresh=0.0

# extra params
num_random=10
max_q_backup=False
deterministic_backup=False

discrete = False
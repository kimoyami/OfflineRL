# Conservative Q-Learning for Offline Reinforcement Learning
# https://arxiv.org/abs/2006.04779
# https://github.com/aviralkumar2907/CQL
import copy

import torch
import numpy as np
from torch import nn
from torch import optim
from loguru import logger

from batchrl.algo.base import BasePolicy
from batchrl.utils.data import to_torch
from batchrl.utils.net.common import Net
from batchrl.utils.net.vae import VAE
from batchrl.utils.net.continuous import Critic
from batchrl.utils.net.tanhpolicy import TanhGaussianPolicy


def algo_init(args):
    logger.info('Run algo_init function')
    
    if args["obs_shape"] and args["action_shape"]:
        obs_shape, action_shape = args["obs_shape"], args["action_shape"]
    elif "task" in args.keys():
        from batchrl.utils.env import get_env_shape,get_env_action_range
        obs_shape, action_shape = get_env_shape(args['task'])
        max_action, _ = get_env_action_range(args["task"])
        args["obs_shape"], args["action_shape"] = obs_shape, action_shape
    else:
        raise NotImplementedError
        
    vae = VAE(state_dim = obs_shape, 
              action_dim = action_shape, 
              latent_dim = action_shape*2, 
              max_action = max_action,
              hidden_size=args["vae_hidden_size"]).to(args['device'])

    vae_opt = optim.Adam(vae.parameters(), lr=args["vae_lr"])
        
    
    net_a = Net(layer_num = args['layer_num'], 
                     state_shape = obs_shape, 
                     hidden_layer_size = args['hidden_layer_size'])
    
    actor = TanhGaussianPolicy(preprocess_net = net_a,
                                action_shape = action_shape*2,
                                hidden_layer_size = args['hidden_layer_size'],
                                conditioned_sigma = True,
                              ).to(args['device'])
    
    actor_optim = optim.Adam(actor.parameters(), lr=args['actor_lr'])
    
    net_c1 = Net(layer_num = args['layer_num'],
                  state_shape = obs_shape,  
                  action_shape = action_shape,
                  concat = True, 
                  hidden_layer_size = args['hidden_layer_size'])
    critic1 = Critic(preprocess_net = net_c1,  
                     hidden_layer_size = args['hidden_layer_size'],
                    ).to(args['device'])
    critic1_optim = optim.Adam(critic1.parameters(), lr=args['critic_lr'])
    
    net_c2 = Net(layer_num = args['layer_num'],
                  state_shape = obs_shape,  
                  action_shape = action_shape,
                  concat = True, 
                  hidden_layer_size = args['hidden_layer_size'])
    critic2 = Critic(preprocess_net = net_c2, 
                     hidden_layer_size = args['hidden_layer_size'],
                    ).to(args['device'])
    critic2_optim = optim.Adam(critic2.parameters(), lr=args['critic_lr'])
    
    if args["use_automatic_entropy_tuning"]:
        if args["target_entropy"]:
            target_entropy = args["target_entropy"]
        else:
            target_entropy = -np.prod(args["action_shape"]).item() 
        log_alpha = torch.zeros(1,requires_grad=True, device=args['device'])
        alpha_optimizer = optim.Adam(
            [log_alpha],
            lr=args["actor_lr"],
        )

    return {
        "vae" : {"net" : vae, "opt" : vae_opt},
        "actor" : {"net" : actor, "opt" : actor_optim},
        "critic1" : {"net" : critic1, "opt" : critic1_optim},
        "critic2" : {"net" : critic2, "opt" : critic2_optim},
        "log_alpha" : {"net" : log_alpha, "opt" : alpha_optimizer, "target_entropy": target_entropy}
    }


class AlgoTrainer(BasePolicy):
    def __init__(self, algo_init, args):
        super(AlgoTrainer, self).__init__(args)
        self.args = args
        
        self.vae = algo_init["vae"]["net"]
        self.vae_opt = algo_init["vae"]["opt"]
        
        self.actor = algo_init["actor"]["net"]
        self.actor_opt = algo_init["actor"]["opt"]
        
        self.critic1 = algo_init["critic1"]["net"]
        self.critic1_opt = algo_init["critic1"]["opt"]
        self.critic2 = algo_init["critic2"]["net"]
        self.critic2_opt = algo_init["critic2"]["opt"]
        
        self.actor_target = copy.deepcopy(self.actor)
        self.critic1_target = copy.deepcopy(self.critic1)
        self.critic2_target = copy.deepcopy(self.critic2)
        
        if args["use_automatic_entropy_tuning"]:
            self.log_alpha = algo_init["log_alpha"]["net"]
            self.alpha_opt = algo_init["log_alpha"]["opt"]
            self.target_entropy = algo_init["log_alpha"]["target_entropy"]
            
        self.critic_criterion = nn.MSELoss()
        
        self._n_train_steps_total = 0
        self._current_epoch = 0
        
    
    def _get_tensor_values(self, obs, actions, network):
        action_shape = actions.shape[0]
        obs_shape = obs.shape[0]
        num_repeat = int (action_shape / obs_shape)
        obs_temp = obs.unsqueeze(1).repeat(1, num_repeat, 1).view(obs.shape[0] * num_repeat, obs.shape[1])
        preds = network(obs_temp, actions)
        preds = preds.view(obs.shape[0], num_repeat, 1)
        return preds

    def _get_policy_actions(self, obs, num_actions, network=None):
        obs_temp = obs.unsqueeze(1).repeat(1, num_actions, 1).view(obs.shape[0] * num_actions, obs.shape[1])
        new_obs_actions,new_obs_log_pi= network(
            obs_temp, reparameterize=True, return_log_prob=True,
        )
        if not self.args["discrete"]:
            return new_obs_actions, new_obs_log_pi.view(obs.shape[0], num_actions, 1)
        else:
            return new_obs_actions
        
    def forward(self, obs, reparameterize=True, return_log_prob=True):
        log_prob = None
        tanh_normal = self.actor(obs,reparameterize=reparameterize,)

        if return_log_prob:
            if reparameterize is True:
                action, pre_tanh_value = tanh_normal.rsample(
                    return_pretanh_value=True
                )
            else:
                action, pre_tanh_value = tanh_normal.sample(
                    return_pretanh_value=True
                )
            log_prob = tanh_normal.log_prob(
                action,
                pre_tanh_value=pre_tanh_value
            )
            log_prob = log_prob.sum(dim=1, keepdim=True)
        else:
            if reparameterize is True:
                action = tanh_normal.rsample()
            else:
                action = tanh_normal.sample()
        return action, log_prob
    
    def get_actor_action(self, obs_next, no_grad=False):
        if no_grad:
            with torch.no_grad():
                action_next_actor = self.actor_target(obs_next).normal_mean
                action_next_vae = self.vae.decode(obs_next, z = action_next_actor)
        else:
            action_next_actor = self.actor_target(obs_next).normal_mean
            action_next_vae = self.vae.decode(obs_next, z = action_next_actor)      
            
        return action_next_vae
        
    def _train(self, batch):
        self._current_epoch += 1
        batch = to_torch(batch, torch.float, device=self.args["device"])
        rewards = batch.rew
        terminals = batch.done
        obs = batch.obs
        actions = batch.act
        obs_next = batch.obs_next
        
        actions = self.get_actor_action(obs, no_grad=True)

        """
        Policy and Alpha Loss
        """
        new_obs_actions, log_pi = self.forward(obs)
        
        if self.args["use_automatic_entropy_tuning"]:
            alpha_loss = -(self.log_alpha * (log_pi + self.target_entropy).detach()).mean()
            self.alpha_opt.zero_grad()
            alpha_loss.backward()
            self.alpha_opt.step()
            alpha = self.log_alpha.exp()
        else:
            alpha_loss = 0
            alpha = 1

        q_new_actions = torch.min(
            self.critic1(obs, new_obs_actions),
            self.critic2(obs, new_obs_actions),
        )

        if self._current_epoch < self.args["policy_bc_steps"]:
            """
            For the initial few epochs, try doing behaivoral cloning, if needed
            conventionally, there's not much difference in performance with having 20k 
            gradient steps here, or not having it
            """
            policy_log_prob = self.actor.log_prob(obs, actions)
            policy_loss = (alpha * log_pi - policy_log_prob).mean()
        else:
            policy_loss = (alpha*log_pi - q_new_actions).mean()
        self.actor_opt.zero_grad()
        policy_loss.backward()
        self.actor_opt.step()
        
        """
        QF Loss
        """
        q1_pred = self.critic1(obs, actions)
        q2_pred = self.critic2(obs, actions)
        
        new_next_actions,new_log_pi= self.forward(
            obs_next, reparameterize=True, return_log_prob=True,
        )
        new_curr_actions, new_curr_log_pi= self.forward(
            obs, reparameterize=True, return_log_prob=True,
        )

        if not self.args["max_q_backup"]:
            target_q_values = torch.min(
                self.critic1_target(obs_next, new_next_actions),
                self.critic2_target(obs_next, new_next_actions),
            )
            
            if not self.args["deterministic_backup"]:
                target_q_values = target_q_values - alpha * new_log_pi
                
        with torch.no_grad():
            action_next_actor = self.actor_target(obs_next).normal_mean
            action_next_vae = self.vae.decode(obs_next, z = action_next_actor)

            target_q1 = self.critic1_target(obs_next, action_next_vae)
            target_q2 = self.critic2_target(obs_next, action_next_vae)

            target_q = self.args["lmbda"] * torch.min(target_q1, target_q2) + (1 - self.args["lmbda"]) * torch.max(target_q1, target_q2)
            q_target = rew + (1 - done) * self.args["discount"] * target_q

        #q_target = self.args["reward_scale"] * rewards + (1. - terminals) * self.args["discount"] * target_q_values.detach()
            
        qf1_loss = self.critic_criterion(q1_pred, q_target)
        qf2_loss = self.critic_criterion(q2_pred, q_target)

        ## add CQL
        random_actions_tensor = torch.FloatTensor(q2_pred.shape[0] * self.args["num_random"], actions.shape[-1]).uniform_(-1, 1).to(self.args["device"])
        curr_actions_tensor, curr_log_pis = self._get_policy_actions(obs, num_actions=self.args["num_random"], network=self.forward)
        new_curr_actions_tensor, new_log_pis = self._get_policy_actions(obs_next, num_actions=self.args["num_random"], network=self.forward)
        q1_rand = self._get_tensor_values(obs, random_actions_tensor, network=self.critic1)
        q2_rand = self._get_tensor_values(obs, random_actions_tensor, network=self.critic2)
        q1_curr_actions = self._get_tensor_values(obs, curr_actions_tensor, network=self.critic1)
        q2_curr_actions = self._get_tensor_values(obs, curr_actions_tensor, network=self.critic2)
        q1_next_actions = self._get_tensor_values(obs, new_curr_actions_tensor, network=self.critic1)
        q2_next_actions = self._get_tensor_values(obs, new_curr_actions_tensor, network=self.critic2)

        cat_q1 = torch.cat([q1_rand, q1_pred.unsqueeze(1), q1_next_actions, q1_curr_actions], 1)
        cat_q2 = torch.cat([q2_rand, q2_pred.unsqueeze(1), q2_next_actions, q2_curr_actions], 1)

        if self.args["min_q_version"] == 3:
            # importance sammpled version
            random_density = np.log(0.5 ** curr_actions_tensor.shape[-1])
            cat_q1 = torch.cat(
                [q1_rand - random_density, q1_next_actions - new_log_pis.detach(), q1_curr_actions - curr_log_pis.detach()], 1
            )
            cat_q2 = torch.cat(
                [q2_rand - random_density, q2_next_actions - new_log_pis.detach(), q2_curr_actions - curr_log_pis.detach()], 1
            )
            
        min_qf1_loss = torch.logsumexp(cat_q1 / self.args["temp"], dim=1,).mean() * self.args["min_q_weight"] * self.args["temp"]
        min_qf2_loss = torch.logsumexp(cat_q2 / self.args["temp"], dim=1,).mean() * self.args["min_q_weight"] * self.args["temp"]
                    
        """Subtract the log likelihood of data"""
        min_qf1_loss = min_qf1_loss - q1_pred.mean() * self.args["min_q_weight"]
        min_qf2_loss = min_qf2_loss - q2_pred.mean() * self.args["min_q_weight"]

        qf1_loss = self.args["explore"]*qf1_loss + (2-self.args["explore"])*min_qf1_loss
        qf2_loss = self.args["explore"]*qf2_loss + (2-self.args["explore"])*min_qf2_loss

        """
        Update critic networks
        """
        self.critic1_opt.zero_grad()
        qf1_loss.backward(retain_graph=True)
        self.critic1_opt.step()

        self.critic2_opt.zero_grad()
        qf2_loss.backward()
        self.critic2_opt.step()

        """
        Soft Updates target network
        """
        self._sync_weight(self.actor_target, self.actor, self.args["soft_target_tau"])
        self._sync_weight(self.critic1_target, self.critic1, self.args["soft_target_tau"])
        self._sync_weight(self.critic2_target, self.critic2, self.args["soft_target_tau"])
        
        self._n_train_steps_total += 1
        
    def get_model(self):
        return self.actor
    
    def save_model(self, model_save_path):
        torch.save(self.actor, model_save_path)
    
    def train(self, buffer, callback_fn):
        self.vae = torch.load("/tmp/vae_499999.pkl").to(self.args["device"])
        self.vae.eval()
        for epoch in range(1,self.args["max_epoch"]+1):
            for step in range(1,self.args["steps_per_epoch"]+1):
                train_data = buffer.sample(self.args["batch_size"])
                self._train(train_data)
            
            res = callback_fn(self.actor)
            self.log_res(epoch, res)

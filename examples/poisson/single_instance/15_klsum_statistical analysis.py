import os
import sys
import json
import torch
import numpy as np

import matplotlib
# matplotlib.use("pgf")
matplotlib.rcParams.update({
    # 'font.family': 'serif',
    'font.size':12,
})
from matplotlib import pyplot as plt

import pytorch_lightning as pl
from pytorch_lightning import Trainer, seed_everything
from pytorch_lightning.loggers import TensorBoardLogger
seed_everything(42)

import DiffNet
from DiffNet.networks.wgan import GoodNetwork
from DiffNet.DiffNetFEM import DiffNet2DFEM

from torch.utils import data
from DiffNet.gen_input_calc import generate_diffusivity_tensor

class Dataset(data.Dataset):
    'PyTorch dataset for sampling coefficients'
    def __init__(self, coeff, domain_size=64):
        """
        Initialization
        """
        self.coeff = coeff
        self.domain_size = domain_size
        self.n_samples = 1000
        self.nu = generate_diffusivity_tensor(self.coeff, output_size=self.domain_size).squeeze()
        # bc1 will be source, u will be set to 1 at these locations
        self.bc1 = np.zeros((domain_size, domain_size))
        self.bc1[:,0] = 1
        # bc2 will be sink, u will be set to 0 at these locations
        self.bc2 = np.zeros((domain_size, domain_size))
        self.bc2[:,-1] = 1
        self.n_samples = 100        

    def __len__(self):
        'Denotes the total number of samples'
        return self.n_samples

    def __getitem__(self, index):
        'Generates one sample of data'
        inputs = np.array([self.nu, self.bc1, self.bc2])
        forcing = np.zeros_like(self.nu)
        return torch.FloatTensor(inputs), torch.FloatTensor(forcing).unsqueeze(0)        


class Poisson(DiffNet2DFEM):
    """docstring for Poisson"""
    def __init__(self, network, dataset, **kwargs):
        super(Poisson, self).__init__(network, dataset, **kwargs)

    def loss(self, u, inputs_tensor, forcing_tensor):

        f = forcing_tensor # renaming variable
        
        # extract diffusivity and boundary conditions here
        nu = inputs_tensor[:,0:1,:,:]
        bc1 = inputs_tensor[:,1:2,:,:]
        bc2 = inputs_tensor[:,2:3,:,:]

        # apply boundary conditions
        u = torch.where(bc1>0.5,1.0+u*0.0,u)
        u = torch.where(bc2>0.5,u*0.0,u)


        nu_gp = self.gauss_pt_evaluation(nu)
        f_gp = self.gauss_pt_evaluation(f)
        u_gp = self.gauss_pt_evaluation(u)
        u_x_gp = self.gauss_pt_evaluation_der_x(u)
        u_y_gp = self.gauss_pt_evaluation_der_y(u)

        transformation_jacobian = self.gpw.unsqueeze(-1).unsqueeze(-1).unsqueeze(0).type_as(nu_gp)
        res_elmwise = transformation_jacobian * (nu_gp * (u_x_gp**2 + u_y_gp**2) - (u_gp * f_gp))
        res_elmwise = torch.sum(res_elmwise, 1) 

        loss = torch.mean(res_elmwise)
        return loss

    def forward(self, batch):
        inputs_tensor, forcing_tensor = batch
        return self.network[0], inputs_tensor, forcing_tensor

    def configure_optimizers(self):
        """
        Configure optimizer for network parameters
        """
        lr = self.learning_rate
        opts = [torch.optim.LBFGS(self.network, lr=1.0, max_iter=5)]
        return opts, []

    def on_epoch_end(self):
        fig, axs = plt.subplots(1, 2, figsize=(2*2,1.2),
                            subplot_kw={'aspect': 'auto'}, sharex=True, sharey=True, squeeze=True)
        for ax in axs:
            ax.set_xticks([])
            ax.set_yticks([])
        self.network.eval()
        inputs, forcing = self.dataset[0]

        u, inputs_tensor, forcing_tensor = self.forward((inputs.unsqueeze(0).type_as(next(self.network.parameters())), forcing.unsqueeze(0).type_as(next(self.network.parameters()))))

        f = forcing_tensor # renaming variable
        
        # extract diffusivity and boundary conditions here
        nu = inputs_tensor[:,0:1,:,:]
        bc1 = inputs_tensor[:,1:2,:,:]
        bc2 = inputs_tensor[:,2:3,:,:]

        # apply boundary conditions
        u = torch.where(bc1>0.5,1.0+u*0.0,u)
        u = torch.where(bc2>0.5,u*0.0,u)



        k = nu.squeeze().detach().cpu()
        u = u.squeeze().detach().cpu()

        im0 = axs[0].imshow(k,cmap='jet')
        fig.colorbar(im0, ax=axs[0])
        im1 = axs[1].imshow(u,cmap='jet')
        fig.colorbar(im1, ax=axs[1])  
        plt.savefig(os.path.join(self.logger[0].log_dir, 'contour_' + str(self.current_epoch) + '.png'))
        self.logger[0].experiment.add_figure('Contour Plots', fig, self.current_epoch)
        plt.close('all')

def main():

    coeffs = np.load('../parametric/sobol_6d.npy')
    stats = {}
    stats['metrics'] = []
    for coeff_idx, coeff in enumerate(coeffs):
        u_tensor = np.ones((1,1,64,64))
        network = torch.nn.ParameterList([torch.nn.Parameter(torch.FloatTensor(u_tensor), requires_grad=True)])
        dataset = Dataset(coeff, domain_size=64)
        basecase = Poisson(network, dataset, batch_size=1)

        # ------------------------
        # 1 INIT TRAINER
        # ------------------------
        logger = pl.loggers.TensorBoardLogger('./klsum_stats/', name=f"{coeff_idx}")
        csv_logger = pl.loggers.CSVLogger(logger.save_dir, name=logger.name, version=logger.version)

        early_stopping = pl.callbacks.early_stopping.EarlyStopping('loss',
            min_delta=1e-8, patience=10, verbose=False, mode='max', strict=True)
        checkpoint = pl.callbacks.model_checkpoint.ModelCheckpoint(monitor='loss',
            dirpath=logger.log_dir, filename='{epoch}-{step}',
            mode='min', save_last=True)

        trainer = Trainer(gpus=[0],callbacks=[early_stopping],
            checkpoint_callback=checkpoint, logger=[logger,csv_logger],
            max_epochs=5, deterministic=True, profiler="simple")

        # ------------------------
        # 4 Training
        # ------------------------

        trainer.fit(basecase)
        stats['metrics'].append(trainer.callback_metrics)
        # ------------------------
        # 5 SAVE NETWORK
        # ------------------------
        torch.save(basecase.network, os.path.join(logger.log_dir, 'network.pt'))
        torch.save(stats, os.path.join(logger.log_dir, 'stats.pt'))


if __name__ == '__main__':
    main()
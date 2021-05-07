import os
import sys
import json
import math
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
from DiffNet.datasets.single_instances.rectangles import RectangleManufactured


class Poisson(DiffNet2DFEM):
    """docstring for Poisson"""
    def __init__(self, network, dataset, **kwargs):
        super(Poisson, self).__init__(network, dataset, **kwargs)
        x = np.linspace(0,1,self.domain_size)
        y = np.linspace(0,1,self.domain_size)
        xx, yy = np.meshgrid(x,y)
        self.u_exact = torch.tensor(np.sin(math.pi*xx)*np.sin(math.pi*yy))


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

        transformation_jacobian = (0.5 * self.h)**2 * self.gpw.unsqueeze(-1).unsqueeze(-1).unsqueeze(0).type_as(nu_gp)
        res_elmwise = transformation_jacobian * ((0.5 * nu_gp * (u_x_gp**2 + u_y_gp**2) - (u_gp * f_gp)))
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
        opts = [torch.optim.Adam(self.network, lr=lr)]
        return opts, []

    def on_epoch_end(self):
        fig, axs = plt.subplots(1, 4, figsize=(2*4,1.2),
                            subplot_kw={'aspect': 'auto'}, sharex=True, sharey=True, squeeze=True)
        for ax in axs:
            ax.set_xticks([])
            ax.set_yticks([])
        self.network.eval()
        inputs, forcing = self.dataset[0]

        u, inputs_tensor, forcing_tensor = self.forward((inputs.unsqueeze(0).type_as(next(self.network.parameters())), forcing.unsqueeze(0).type_as(next(self.network.parameters()))))

        f = forcing_tensor.squeeze().detach().cpu() # renaming variable
        
        # extract diffusivity and boundary conditions here
        nu = inputs_tensor[:,0:1,:,:]
        bc1 = inputs_tensor[:,1:2,:,:]
        bc2 = inputs_tensor[:,2:3,:,:]

        # apply boundary conditions
        u = torch.where(bc1>0.5,1.0+u*0.0,u)
        u = torch.where(bc2>0.5,u*0.0,u)


        k = nu.squeeze().detach().cpu()
        u = u.squeeze().detach().cpu()
        u_exact = self.u_exact.squeeze().detach().cpu()
        diff = u - u_exact
        np.set_printoptions(precision=3)
        print(np.around(np.linalg.norm(diff.flatten())/self.domain_size, 4))
        im0 = axs[0].imshow(f,cmap='jet')
        fig.colorbar(im0, ax=axs[0], ticks=[0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0])
        im1 = axs[1].imshow(u,cmap='jet', vmin=0.0, vmax=1.0)
        fig.colorbar(im1, ax=axs[1])
        im2 = axs[2].imshow(u_exact,cmap='jet', vmin=0.0, vmax=1.0)
        fig.colorbar(im2, ax=axs[2])
        im3 = axs[3].imshow(diff,cmap='jet')
        fig.colorbar(im3, ax=axs[3])
        plt.savefig(os.path.join(self.logger[0].log_dir, 'contour_' + str(self.current_epoch) + '.png'))
        self.logger[0].experiment.add_figure('Contour Plots', fig, self.current_epoch)
        plt.close('all')

def main():
    u_tensor = np.ones((1,1,1024,1024))
    network = torch.nn.ParameterList([torch.nn.Parameter(torch.FloatTensor(u_tensor), requires_grad=True)])
    dataset = RectangleManufactured(domain_size=1024)
    basecase = Poisson(network, dataset, batch_size=1, domain_size=1024)

    # ------------------------
    # 1 INIT TRAINER
    # ------------------------
    logger = pl.loggers.TensorBoardLogger('.', name="manufactured")
    csv_logger = pl.loggers.CSVLogger(logger.save_dir, name=logger.name, version=logger.version)

    early_stopping = pl.callbacks.early_stopping.EarlyStopping('loss',
        min_delta=1e-8, patience=10, verbose=False, mode='auto', strict=True)
    checkpoint = pl.callbacks.model_checkpoint.ModelCheckpoint(monitor='loss',
        dirpath=logger.log_dir, filename='{epoch}-{step}',
        mode='min', save_last=True)

    trainer = Trainer(gpus=[0],callbacks=[early_stopping],
        checkpoint_callback=checkpoint, logger=[logger,csv_logger],
        max_epochs=1000, deterministic=True, profiler=True)

    # ------------------------
    # 4 Training
    # ------------------------

    trainer.fit(basecase)

    # ------------------------
    # 5 SAVE NETWORK
    # ------------------------
    torch.save(basecase.network, os.path.join(logger.log_dir, 'network.pt'))


if __name__ == '__main__':
    main()
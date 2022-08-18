import os
import torch
from torch.optim.lr_scheduler import ReduceLROnPlateau
import torch.nn.functional as F
import pytorch_lightning as pl
from torchmetrics.detection.mean_ap import MeanAveragePrecision
from .fpn import TwoWayFpn
from .backbone import generate_backbone_EfficientPS, output_feature_size
from .instance_head import InstanceHead
from .instance_predictions import instance_predictions
import os.path
import numpy as np

class Instance(pl.LightningModule):
    """
    EfficientPS model see http://panoptic.cs.uni-freiburg.de/
    Here pytorch lightningis used https://pytorch-lightning.readthedocs.io/en/latest/
    """

    def __init__(self, cfg):
        """
        Args:
        - cfg (Config) : Config object from detectron2
        """
        super().__init__()
        self.save_hyperparameters()
        self.learning_rate = cfg.SOLVER.BASE_LR_INSTANCE
        self.cfg = cfg
        self.backbone = generate_backbone_EfficientPS(cfg)
        self.fpn = TwoWayFpn(
            output_feature_size[cfg.MODEL_CUSTOM.BACKBONE.EFFICIENTNET_ID])
        self.instance_head = InstanceHead(cfg)
        self.valid_acc_bbx = MeanAveragePrecision()
        # self.valid_acc_sgm = MeanAveragePrecision(iou_type="segm")
        
        # self.epoch = 0
    
    def on_train_start(self):
        self.logger.log_hyperparams(self.hparams)

    def forward(self, x):
        # in lightning, forward defines the prediction/inference actions
        predictions, _ = self.shared_step(x)
        return predictions

    def training_step(self, batch, batch_idx):
        # training_step defined the train loop.
        # It is independent of forward
        _, loss = self.shared_step(batch)
        
        # Add losses to logs
        [self.log(k, v, batch_size=self.cfg.BATCH_SIZE, on_step=False, on_epoch=True) for k,v in loss.items()]
        self.log('train_loss', sum(loss.values()), batch_size=self.cfg.BATCH_SIZE, on_step=True, on_epoch=True)
        return {'loss': sum(loss.values())}

    def shared_step(self, inputs):
        loss = dict()
        predictions = dict()
        # Feature extraction
        features = self.backbone.extract_endpoints(inputs['image'])
        pyramid_features = self.fpn(features)
        # Heads Predictions
        pred_instance, instance_losses = self.instance_head(pyramid_features, inputs)
        # Output set up
        loss.update(instance_losses)
        predictions.update({'instance': pred_instance, "loss": instance_losses})
        return predictions, loss

    # def validation_step(self, batch, batch_idx):
        
    #     predictions, _ = self.shared_step(batch)
        
    #     target = [dict(
    #             boxes=instance.get("gt_boxes").tensor,
    #             labels=instance.get("gt_classes"),
    #             masks=instance.get("gt_masks").tensor
    #         ) for instance in batch["instance"]]
        
    #     if predictions["instance"] != None:       

    #         #TODO: scale masks
    #         preds = [dict(
    #             boxes=instance.get("pred_boxes").tensor,
    #             labels=instance.get("pred_classes"),
    #             # masks=instance.get("pred_masks").to(torch.uint8),
    #             scores=instance.get("scores")
    #         ) for instance in predictions["instance"]]
            
    #     else:
    #         preds = [dict(
    #             boxes=torch.zeros((0, 4), dtype=torch.float).to(self.device),
    #             labels=torch.zeros((0), dtype=torch.long).to(self.device),
    #             # masks=instance.get("pred_masks").to(torch.uint8),
    #             scores=torch.zeros((0), dtype=torch.float).to(self.device)
    #         ) for _ in batch["instance"]]
        
    #     # Metric
    #     self.valid_acc_bbx.update(preds, target)
        
    # def validation_epoch_end(self, outputs):
    #     self.log_dict(self.valid_acc_bbx.compute(), sync_dist=True)
    #     self.valid_acc_bbx.reset()

    def predict_step(self, batch, batch_idx, dataloader_idx=0):

        # Feature extraction
        features = self.backbone.extract_endpoints(batch['image'])
        pyramid_features = self.fpn(features)
        # Heads Predictions
        pred_instance, _ = self.instance_head(pyramid_features)

        return {
            'preds': pred_instance,
            'targets': batch["instance"],
            'image_id': batch['image_id'],
            'images': batch['image']
        }
        
    
    def on_predict_epoch_end(self, results):
        #Save Panoptic results
        print("saving instance results")
        instance_predictions(self.cfg, results[0])
        

    def configure_optimizers(self):
        print("Optimizer - using {} with lr {}".format(self.cfg.SOLVER.NAME, self.cfg.SOLVER.BASE_LR_INSTANCE))
        if self.cfg.SOLVER.NAME == "Adam":
            self.optimizer = torch.optim.Adam(self.parameters(),
                                         lr=self.learning_rate,
                                         weight_decay=self.cfg.SOLVER.WEIGHT_DECAY)
        elif self.cfg.SOLVER.NAME == "SGD":
            self.optimizer = torch.optim.SGD(self.parameters(),
                                        lr=self.learning_rate,
                                        momentum=0.9,
                                        weight_decay=self.cfg.SOLVER.WEIGHT_DECAY)
        else:
            raise ValueError("Solver name is not supported, \
                Adam or SGD : {}".format(self.cfg.SOLVER.NAME))
        return {
            'optimizer': self.optimizer,
            'lr_scheduler': ReduceLROnPlateau(self.optimizer,
                                              mode='min',
                                              patience=10,
                                              factor=0.1,
                                              min_lr=self.cfg.SOLVER.BASE_LR_INSTANCE*1e-4,
                                              verbose=True),
            'monitor': 'train_loss_epoch'
        }

    def optimizer_step(self, current_epoch, batch_nb, optimizer, optimizer_idx, closure, on_tpu=False, using_native_amp=False, using_lbfgs=False):
        # warm up lr
        if self.trainer.global_step < self.cfg.SOLVER.WARMUP_ITERS:
            lr_scale = min(1., float(self.trainer.global_step + 1) /
                                    float(self.cfg.SOLVER.WARMUP_ITERS))
            for pg in optimizer.param_groups:
                pg['lr'] = lr_scale * self.cfg.SOLVER.BASE_LR_INSTANCE

        # update params
        optimizer.step(closure=closure)
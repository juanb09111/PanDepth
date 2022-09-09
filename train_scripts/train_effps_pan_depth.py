import torch
import numpy as np
import os
import logging
import pytorch_lightning as pl
from pytorch_lightning.callbacks import (
    EarlyStopping,
    ModelCheckpoint,
    LearningRateMonitor
)
from pytorch_lightning.utilities.model_summary  import ModelSummary
from pytorch_lightning import loggers as pl_loggers
from detectron2.config import get_cfg
from detectron2.utils.events import _CURRENT_STORAGE_STACK, EventStorage


from efficientps import Pan_Depth
from utils.add_custom_params import add_custom_params
from datasets.vkitti_depth_dataset import get_dataloaders



def train(args):
    
    # Retrieve Config and and custom base parameter
    cfg = get_cfg()
    add_custom_params(cfg)
    cfg.merge_from_file(args.config)
    cfg.NUM_GPUS = torch.cuda.device_count()
    
    logging.getLogger("pytorch_lightning").setLevel(logging.INFO)
    logger = logging.getLogger("pytorch_lightning.core")
    if not os.path.exists(cfg.CALLBACKS.CHECKPOINT_DIR):
        os.makedirs(cfg.CALLBACKS.CHECKPOINT_DIR)
    logger.addHandler(logging.FileHandler(
        os.path.join(cfg.CALLBACKS.CHECKPOINT_DIR,"core_pan_depth.log"), mode='w'))
    with open(args.config) as file:
        logger.info(file.read())
    # Initialise Custom storage to avoid error when using detectron 2
    _CURRENT_STORAGE_STACK.append(EventStorage())

    #Get dataloaders
    if cfg.DATASET_TYPE == "vkitti2":
        train_loader, valid_loader, _ = get_dataloaders(cfg)

    # Create model or load a checkpoint
    if os.path.exists(cfg.CHECKPOINT_PATH_TRAINING):
        print('""""""""""""""""""""""""""""""""""""""""""""""')
        print("Loading model from {}".format(cfg.CHECKPOINT_PATH_TRAINING))
        print('""""""""""""""""""""""""""""""""""""""""""""""')
        efficientps = Pan_Depth.load_from_checkpoint(cfg=cfg,
            checkpoint_path=cfg.CHECKPOINT_PATH_TRAINING, learning_rate=cfg.SOLVER.BASE_LR_PAN_DEPTH)
    else:
        print('""""""""""""""""""""""""""""""""""""""""""""""')
        print("Creating a new model")
        print('""""""""""""""""""""""""""""""""""""""""""""""')
        efficientps = Pan_Depth(cfg)
        cfg.CHECKPOINT_PATH_TRAINING = None

    # logger.info(efficientps.print)
    ModelSummary(efficientps, max_depth=-1)
    # Callbacks / Hooks
    early_stopping = EarlyStopping('IoU', patience=30, mode='max')
    checkpoint = ModelCheckpoint(monitor='IoU',
                                 mode='max',
                                 dirpath=cfg.CALLBACKS.CHECKPOINT_DIR,
                                 save_last=True,
                                 verbose=True)

    lr_monitor = LearningRateMonitor(logging_interval='epoch')

    #logger
    tb_logger = pl_loggers.TensorBoardLogger("tb_logs_2", name="effps_pan_depth_depth_only")
    # Create a pytorch lighting trainer
    trainer = pl.Trainer(
        # weights_summary='full',
        logger=tb_logger,
        auto_lr_find=args.tune,
        log_every_n_steps=np.floor(len(train_loader)/2),
        devices=1 if args.tune else list(range(torch.cuda.device_count())),
        strategy=None if args.tune else "ddp",
        accelerator='gpu',
        num_sanity_val_steps=0,
        fast_dev_run=cfg.SOLVER.FAST_DEV_RUN if args.fast_dev else False,
        callbacks=[early_stopping, checkpoint, lr_monitor],
        # precision=cfg.PRECISION,
        resume_from_checkpoint=cfg.CHECKPOINT_PATH_TRAINING,
        # gradient_clip_val=0,
        accumulate_grad_batches=cfg.SOLVER.ACCUMULATE_GRAD
    )
    logger.addHandler(logging.StreamHandler())
    if args.tune:
        lr_finder = trainer.tuner.lr_find(efficientps, train_loader, valid_loader, min_lr=1e-4, max_lr=0.1, num_training=100)
        print("LR found:", lr_finder.suggestion())
    else:
        trainer.fit(efficientps, train_loader, val_dataloaders=valid_loader)
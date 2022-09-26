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

from detectron2.modeling import build_model
from efficientps import Instance
from utils.add_custom_params import add_custom_params

from datasets.fridge_objects_dataset import EfficientDetDataModule
from datasets.odFridgeObjects_cats import obj_categories as odFridgeObjects_cats

from datasets.vkitti_depth_datamodule import VkittiDataModule
from datasets.vkitti_cats import obj_categories as vkitti_cats


def train(args):
    
    # Retrieve Config and and custom base parameter
    cfg = get_cfg()
    add_custom_params(cfg)
    cfg.merge_from_file(args.config)
    cfg.NUM_GPUS = torch.cuda.device_count()
    
    logging.getLogger("pytorch_lightning").setLevel(logging.INFO)
    logger = logging.getLogger("pytorch_lightning.core")
    # if not os.path.exists(cfg.CALLBACKS.CHECKPOINT_DIR):
    #     os.makedirs(cfg.CALLBACKS.CHECKPOINT_DIR)
    # logger.addHandler(logging.FileHandler(
    #     os.path.join(cfg.CALLBACKS.CHECKPOINT_DIR,"core_semantic.log"), mode='w'))
    # with open(args.config) as file:
    #     logger.info(file.read())
    # Initialise Custom storage to avoid error when using detectron 2
    _CURRENT_STORAGE_STACK.append(EventStorage())

    if cfg.DATASET_TYPE == "vkitti2":
        datamodule = VkittiDataModule(cfg)
        obj_categories = vkitti_cats

    
    elif cfg.DATASET_TYPE == "odFridgeObjects":
        img_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "datasets", "odFridgeObjects", "images")
        annotation_dir =os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "datasets", "odFridgeObjects", "ann_clean.json")
        img_size = 512
        datamodule = EfficientDetDataModule(
            img_dir=img_dir,
            annotation_dir=annotation_dir,
            num_workers=8,
            batch_size=8,
            img_size=img_size,
        )
        obj_categories = odFridgeObjects_cats

    # Create model or load a checkpoint
    if os.path.exists(cfg.CHECKPOINT_PATH_TRAINING):
        print('""""""""""""""""""""""""""""""""""""""""""""""')
        print("Loading model from {}".format(cfg.CHECKPOINT_PATH_TRAINING))
        print('""""""""""""""""""""""""""""""""""""""""""""""')
        maskrcnn = Instance.load_from_checkpoint(cfg=cfg,
            checkpoint_path=cfg.CHECKPOINT_PATH_TRAINING, categories=obj_categories)
    else:
        print('""""""""""""""""""""""""""""""""""""""""""""""')
        print("Creating a new model")
        print('""""""""""""""""""""""""""""""""""""""""""""""')
        maskrcnn = Instance(cfg, categories=obj_categories)
        cfg.CHECKPOINT_PATH_TRAINING = None

    # logger.info(efficientps.print)
    ModelSummary(maskrcnn, max_depth=-1)
    # Callbacks / Hooks
    early_stopping = EarlyStopping('train_loss_epoch', patience=30, mode='min')
    checkpoint = ModelCheckpoint(monitor='train_loss_epoch',
                                 mode='min',
                                 dirpath=cfg.CALLBACKS.CHECKPOINT_DIR,
                                 save_last=True,
                                 verbose=True)

    lr_monitor = LearningRateMonitor(logging_interval='epoch')

    #logger
    tb_logger = pl_loggers.TensorBoardLogger("tb_logs_2", name="maskrcnn_vkitti_scoreTh0.5_nms0.5_min200")
    # Create a pytorch lighting trainer
    trainer = pl.Trainer(
        # weights_summary='full',
        logger=tb_logger,
        auto_lr_find=args.tune,
        log_every_n_steps=2,
        devices=1 if args.tune else list(range(torch.cuda.device_count())),
        strategy=None if args.tune else "ddp",
        accelerator='gpu',
        num_sanity_val_steps=0,
        # fast_dev_run=cfg.SOLVER.FAST_DEV_RUN if args.fast_dev else False,
        callbacks=[early_stopping, checkpoint, lr_monitor],
        # precision=cfg.PRECISION,
        resume_from_checkpoint=cfg.CHECKPOINT_PATH_TRAINING,
        # gradient_clip_val=0,
        # accumulate_grad_batches=cfg.SOLVER.ACCUMULATE_GRAD
    )
    logger.addHandler(logging.StreamHandler())

    if args.tune:
        lr_finder = trainer.tuner.lr_find(maskrcnn, datamodule, min_lr=1e-4, max_lr=0.1, num_training=100)
        print("LR found:", lr_finder.suggestion())
    else:
        trainer.fit(maskrcnn, datamodule)
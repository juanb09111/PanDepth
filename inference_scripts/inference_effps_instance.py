import os
import logging
import torch
import pytorch_lightning as pl
from pytorch_lightning.utilities.model_summary import ModelSummary
from detectron2.config import get_cfg
from detectron2.utils.events import _CURRENT_STORAGE_STACK, EventStorage


from efficientps import Instance
from utils.add_custom_params import add_custom_params
from datasets.vkitti_dataset import get_dataloaders



def inference(args):
    
    # Retrieve Config and and custom base parameter
    cfg = get_cfg()
    add_custom_params(cfg)
    cfg.merge_from_file(args.config)

    logging.getLogger("pytorch_lightning").setLevel(logging.INFO)
    logger = logging.getLogger("pytorch_lightning.core")
    if not os.path.exists(cfg.CALLBACKS.CHECKPOINT_DIR):
        os.makedirs(cfg.CALLBACKS.CHECKPOINT_DIR)
    logger.addHandler(logging.FileHandler(
        os.path.join(cfg.CALLBACKS.CHECKPOINT_DIR,"core.log"), mode='w'))
    # with open(args.config) as file:
    #     logger.info(file.read())
    # Initialise Custom storage to avoid error when using detectron 2
    _CURRENT_STORAGE_STACK.append(EventStorage())

    #Get dataloaders
    if cfg.DATASET_TYPE == "vkitti2":
        train_loader, valid_loader, _ = get_dataloaders(cfg)

    # Create model or load a checkpoint
    if os.path.exists(cfg.CHECKPOINT_PATH_INFERENCE):
        print('""""""""""""""""""""""""""""""""""""""""""""""')
        print("Loading model from {}".format(cfg.CHECKPOINT_PATH_INFERENCE))
        print('""""""""""""""""""""""""""""""""""""""""""""""')
        efficientps_instance = Instance.load_from_checkpoint(cfg=cfg,
            checkpoint_path=cfg.CHECKPOINT_PATH_INFERENCE)
    else:
        print('""""""""""""""""""""""""""""""""""""""""""""""')
        print("Creating a new model")
        print('""""""""""""""""""""""""""""""""""""""""""""""')
        efficientps_instance = Instance(cfg)
        cfg.CHECKPOINT_PATH_INFERENCE = None

    ModelSummary(efficientps_instance, max_depth=-1) 
    trainer = pl.Trainer(
         # weights_summary='full',
        auto_lr_find=args.tune,
        log_every_n_steps=1276,
        devices=1 if args.tune else list(range(torch.cuda.device_count())),
        strategy=None if args.tune else "ddp",
        accelerator='gpu',
        num_sanity_val_steps=0,
        fast_dev_run=cfg.SOLVER.FAST_DEV_RUN if args.fast_dev else False,
        # precision=cfg.PRECISION,
        resume_from_checkpoint=cfg.CHECKPOINT_PATH_INFERENCE,
        # gradient_clip_val=0,
        accumulate_grad_batches=cfg.SOLVER.ACCUMULATE_GRAD
    )
    logger.addHandler(logging.StreamHandler())

    
    efficientps_instance.eval()
    with torch.no_grad():
        predictions = trainer.predict(efficientps_instance, dataloaders=train_loader)

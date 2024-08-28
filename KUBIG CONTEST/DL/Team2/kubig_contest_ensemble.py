# -*- coding: utf-8 -*-
"""Kubig contest_ensemble

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/18eTa7FKzVuKRqCiVhi54R0rivHG3vktG
"""

!pip install --quiet timm pytorch_lightning torchmetrics

from google.colab import drive
drive.mount('/content/drive')

import os
import gc
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
import torchvision
import pytorch_lightning as L
import zipfile, shutil

from tensorflow.python.framework.ops import enable_eager_execution
from glob import glob
from tqdm.auto import tqdm
from sklearn.metrics import f1_score
from sklearn.preprocessing import LabelEncoder
from sklearn.model_selection import StratifiedKFold
from torchvision.io import read_image
from torchvision.transforms import v2 as  transforms
from torch.utils.data import Dataset, DataLoader
from pytorch_lightning.callbacks import ModelCheckpoint
from pytorch_lightning.callbacks.early_stopping import EarlyStopping
from torchvision.transforms import RandomAffine, RandomHorizontalFlip, RandomVerticalFlip, ColorJitter
import timm

!cd /content/drive/MyDrive

train_df = pd.read_csv('/content/drive/MyDrive/data/train.csv')
train_df['img_path'] = train_df['img_path'].apply(lambda x: os.path.join('./data', x))
train_df['upscale_img_path'] = train_df['upscale_img_path'].apply(lambda x: os.path.join('./data', x))
le = LabelEncoder()
train_df['class'] = le.fit_transform(train_df['label'])

class CustomDataset(Dataset):
    def __init__(self, df, path_col,  mode='train'):
        self.df = df
        self.path_col = path_col
        self.mode = mode

    def __len__(self):
        return len(self.df)

    def __getitem__(self, idx):
        if self.mode == 'train':
            row = self.df.iloc[idx]
            image = read_image(row[self.path_col])/256.
            label = row['class']
            data = {
                'image':image,
                'label':label
            }
            return data
        elif self.mode == 'val':
            row = self.df.iloc[idx]
            image = read_image(row[self.path_col])/256.
            label = row['class']
            data = {
                'image':image,
                'label':label
            }
            return data
        elif self.mode == 'inference':
            row = self.df.iloc[idx]
            image = read_image(row[self.path_col])/256.
            data = {
                'image':image,
            }
            return data

    def train_transform(self, image):
        pass

class CustomCollateFn:
    def __init__(self, transform, mode):
        self.mode = mode
        self.transform = transform

    def __call__(self, batch):
        if self.mode=='train':
            pixel_values = torch.stack([self.transform(data['image']) for data in batch])
            label = torch.LongTensor([data['label'] for data in batch])
            return {
                'pixel_values':pixel_values,
                'label':label,
            }
        elif self.mode=='val':
            pixel_values = torch.stack([self.transform(data['image']) for data in batch])
            label = torch.LongTensor([data['label'] for data in batch])
            return {
                'pixel_values':pixel_values,
                'label':label,
            }
        elif self.mode=='inference':
            pixel_values = torch.stack([self.transform(data['image']) for data in batch])
            return {
                'pixel_values':pixel_values,
            }

class CustomModel(nn.Module):
    def __init__(self, model):
        super(CustomModel, self).__init__()
        self.model = model
        self.clf = nn.Sequential(
            nn.Tanh(),
            nn.LazyLinear(25),
        )

#     @torch.compile
    def forward(self, x, label=None):
        x = self.model(x)
        x = self.clf(x)
        loss = None
        if label is not None:
            loss = nn.CrossEntropyLoss()(x, label)
        probs = nn.LogSoftmax(dim=-1)(x)
        return probs, loss

class LitCustomModel(L.LightningModule):
    def __init__(self, model):
        super().__init__()
        self.model = CustomModel(model)
        self.validation_step_output = []

    def configure_optimizers(self):
        opt = torch.optim.AdamW(self.parameters(), lr=1e-5)
        return opt

    def training_step(self, batch, batch_idx=None):
        x = batch['pixel_values']
        label = batch['label']
        probs, loss = self.model(x, label)
        self.log(f"train_loss", loss, on_step=True, on_epoch=False)
        return loss

    def validation_step(self, batch, batch_idx=None):
        x = batch['pixel_values']
        label = batch['label']
        probs, loss = self.model(x, label)
        self.validation_step_output.append([probs,label, loss])
        return loss

    def predict_step(self, batch, batch_idx=None):
        x = batch['pixel_values']
        probs, _ = self.model(x)
        return probs

    def on_validation_epoch_end(self):
        pred = torch.cat([x for x, _, _ in self.validation_step_output]).cpu().detach().numpy().argmax(1)
        label = torch.cat([label for _, label, _ in self.validation_step_output]).cpu().detach().numpy()
        score = f1_score(label, pred, average='macro')

        # Calculate validation loss
        val_loss = torch.stack([loss for _, _, loss in self.validation_step_output]).mean()

        self.log("val_score", score)
        self.log("val_loss", val_loss)  # Log validation loss
        self.validation_step_output.clear()
        return score

SEED = 42
N_SPLIT = 3
BATCH_SIZE = 6
L.seed_everything(SEED)

train_df.head()

# img_path 칼럼 코랩마운트에 맞게 수정
base_path = '/content/drive/MyDrive'

# 'img_path'와 'upscale_img_path' 컬럼의 경로를 수정합니다.
train_df['img_path'] = base_path + train_df['img_path'].str[1:]
train_df['upscale_img_path'] = base_path + train_df['upscale_img_path'].str[1:]
train_df

le = LabelEncoder()
train_df['class'] = le.fit_transform(train_df['label'])
if not len(train_df) == len(os.listdir('/content/drive/MyDrive/data/train')):
    raise ValueError()

print(len(train_df))
print(len(os.listdir('/content/drive/MyDrive/data/train')))

skf = StratifiedKFold(n_splits=N_SPLIT, random_state=SEED, shuffle=True)

torch.set_float32_matmul_precision('medium')

test_df = pd.read_csv('/content/drive/MyDrive/data/test.csv')
test_df['img_path'] = test_df['img_path'].apply(lambda x: os.path.join('./data', x))

test_df['img_path'] = base_path + test_df['img_path'].str[1:]

test_df.head()

if not len(test_df) == len(os.listdir('/content/drive/MyDrive/data/test')):
    raise ValueError()

test_transform_448 = transforms.Compose([
    transforms.Resize(size=(448,448), interpolation=transforms.InterpolationMode.BICUBIC),
    transforms.Normalize(mean=(0.485,0.456,0.406), std=(0.229,0.224,0.225)),
])

test_collate_fn_448 = CustomCollateFn(test_transform_448, 'inference')
test_dataset = CustomDataset(test_df, 'img_path', mode='inference')
test_dataloader_448 = DataLoader(test_dataset, collate_fn=test_collate_fn_448, batch_size=BATCH_SIZE*2, num_workers=11)


test_transform_224 = transforms.Compose([
    transforms.Resize(size=(224,224), interpolation=transforms.InterpolationMode.BICUBIC),
    transforms.Normalize(mean=(0.485,0.456,0.406), std=(0.229,0.224,0.225)),
])

test_collate_fn_224 = CustomCollateFn(test_transform_224, 'inference')
test_dataset = CustomDataset(test_df, 'img_path', mode='inference')
test_dataloader_224 = DataLoader(test_dataset, collate_fn=test_collate_fn_224, batch_size=BATCH_SIZE*2, num_workers=11)

test_transform_256 = transforms.Compose([
    transforms.Resize(size=(256,256), interpolation=transforms.InterpolationMode.BICUBIC),
    transforms.Normalize(mean=(0.485,0.456,0.406), std=(0.229,0.224,0.225)),
])

test_collate_fn_256 = CustomCollateFn(test_transform_256, 'inference')
test_dataset = CustomDataset(test_df, 'img_path', mode='inference')
test_dataloader_256 = DataLoader(test_dataset, collate_fn=test_collate_fn_256, batch_size=BATCH_SIZE*2, num_workers=11)

def load_model_predictions(model_name, checkpoint_paths, test_dataloader):
    fold_preds = []
    for checkpoint_path in checkpoint_paths:
        model = timm.create_model(model_name, pretrained=True)
        lit_model = LitCustomModel.load_from_checkpoint(checkpoint_path, model=model)
        trainer = L.Trainer(accelerator='auto', precision=32)
        preds = trainer.predict(lit_model, test_dataloader)
        preds = torch.cat(preds, dim=0).detach().cpu().numpy().argmax(1)
        fold_preds.append(preds)
    return np.stack(fold_preds)

beitv2_checkpoints = glob('/content/drive/MyDrive/checkpoints/beitv2-base-patch16-224*.ckpt')
beitv2_preds  = load_model_predictions('beitv2_base_patch16_224', beitv2_checkpoints, test_dataloader_224)

swinv2_checkpoints = glob('/content/drive/MyDrive/checkpoints/swinv2-base-window16-256-2*.ckpt')
swinv2_preds = load_model_predictions('swinv2_base_window16_256', swinv2_checkpoints, test_dataloader_256)

caformer_checkpoints = glob('/content/drive/MyDrive/checkpoints/caformer-b36*.ckpt')
caformer_preds = load_model_predictions('caformer_b36.sail_in22k_ft_in1k', caformer_checkpoints, test_dataloader_224)

eva02_checkpoints = glob('/content/drive/MyDrive/checkpoints/eva02-base*.ckpt')
eva02_preds = load_model_predictions('eva02_base_patch14_448.mim_in22k_ft_in1k', eva02_checkpoints, test_dataloader_448)

from scipy.stats import mode

beitv2_preds_en = list(map(lambda x: np.bincount(x).argmax(), np.stack(beitv2_preds, axis=1)))
caformer_preds_en = list(map(lambda x: np.bincount(x).argmax(), np.stack(caformer_preds, axis=1)))
swinv2_preds_en = list(map(lambda x: np.bincount(x).argmax(), np.stack(swinv2_preds, axis=1)))
eva02_preds_en = list(map(lambda x: np.bincount(x).argmax(), np.stack(eva02_preds, axis=1)))


# 예측 결합
all_preds = np.stack([beitv2_preds_en, caformer_preds_en, swinv2_preds_en, eva02_preds_en], axis=0)

# Hard Voting: 각 샘플에 대해 가장 많이 나온 클래스 레이블을 선택
final_predictions = mode(all_preds, axis=0).mode.flatten()


# 제출 파일 준비
submission = pd.read_csv('/content/drive/MyDrive/data/sample_submission.csv')
submission['label'] = le.inverse_transform(final_predictions)
submission.to_csv('/content/drive/MyDrive/data/hard_voting_ensemble_submission_2.csv', index=False)
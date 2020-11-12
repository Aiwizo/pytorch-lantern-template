from functools import partial
from pathlib import Path
import numpy as np
import random
import argparse
import torch
import torch.nn.functional as F
import ignite
import logging
import workflow
from workflow.functional import starcompose
from workflow.torch import set_seeds
from workflow.ignite import worker_init
from workflow.ignite.handlers.learning_rate import (
    LearningRateScheduler, warmup, cyclical
)
from datastream import Datastream

from {{cookiecutter.package_name}} import (
    datastream, architecture, metrics, log_examples
)


def train(config):
    set_seeds(config['seed'])
    device = torch.device('cuda' if config['use_cuda'] else 'cpu')

    model = architecture.Model().to(device)
    optimizer = torch.optim.Adam(
        model.parameters(), lr=config['learning_rate']
    )

    train_state = dict(model=model, optimizer=optimizer)

    if Path('model').exists():
        print('Loading model checkpoint')
        workflow.ignite.handlers.ModelCheckpoint.load(
            train_state, 'model/checkpoints', device
        )
        workflow.torch.set_learning_rate(optimizer, config['learning_rate'])

    evaluate_data_loaders = {
        f'evaluate_{name}': datastream.data_loader(
            batch_size=config['eval_batch_size'],
            num_workers=config['n_workers'],
            collate_fn=tuple,
        )
        for name, datastream in datastream.evaluate_datastreams().items()
    }

    gradient_data_loader = (
        datastream.GradientDatastream()
        .data_loader(
            batch_size=config['batch_size'],
            num_workers=config['n_workers'],
            n_batches_per_epoch=config['n_batches_per_epoch'],
            worker_init_fn=partial(worker_init, config['seed'], trainer),
            collate_fn=tuple,
        )
    )

    tensorboard_logger = torch.utils.tensorboard.SummaryWriter()
    early_stopping = workflow.EarlyStopping()
    # gradient_metrics = metrics.gradient_metrics()

    for epoch in tqdm(range(config['max_epochs'])):

        with workflow.torch.module_train(model):
            for examples in workflow.ProgressBar(
                gradient_data_loader
                # gradient_data_loader, metrics=gradient_metrics[['loss']]
            ):
                predictions = model.predictions(
                    architecture.FeatureBatch.from_examples(examples)
                )
                loss = predictions.loss(examples)
                loss.backward()
                optimizer.step()
                optimizer.zero_grad()

                # gradient_metrics = gradient_metrics.update(
                #     examples, predictions, loss
                # )
                # gradient_metrics.log_()

                # optional: schedule learning rate

        with workflow.torch.module_eval(model), torch.no_grad:
            for name, data_loader in evaluate_data_loaders:
                # evaluate_metrics = metrics.evaluate_metrics()

                for examples in tqdm(data_loader):
                    predictions = model.predictions(
                        architecture.FeatureBatch.from_examples(examples)
                    )
                    loss = predictions.loss(examples)

                #     evaluate_metrics = evaluate_metrics.update(
                #         examples, predictions, loss
                #     )
                # evaluate_metrics.log_()

        early_stopping = early_stopping.score(tensorboard_logger)
        if early_stopping.scores_since_improvement == 0:
            torch.save(train_state, 'model_checkpoint.pt')
        elif early_stopping.scores_since_improvement > patience:
            break

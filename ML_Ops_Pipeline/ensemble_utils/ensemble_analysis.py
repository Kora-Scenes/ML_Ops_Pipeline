"""
ensemble analysis

Runs analysis on all the enseble ensembles.
Loads in the datasets and outputs from the individual ensembles.
Runs the inputs through all the enseble ensembles and verifies outputs
against the dataset ground truth
"""

import torch

import glob
import pickle
import os
import time
import inspect
from datetime import datetime
#from multiprocessing import Pool
#import multiprocessing

import json
import mlflow

from ..all_pipelines import get_all_inputs
from ..pipeline_input import source_hash
from ..constants import DATASET_DIR, ENSEMBLE_TESTING, MODEL_TESTING
from ..history import local_history
from .ensemble_visualizer_loop import vizualize_ensemble

import traceback

def analyze_ensemble(pipeline_name, ensemble_name, interpreter_name, dataset_dir, model_classes, interpreters, task_id, ensemble_last_modified, task_id_source_hash, ensemble_last_source_hash, ensemble_classes, visualizers):
	print("-"*10)
	print("ensemble_name:\t",ensemble_name)
	print("interpreter_name:\t",interpreter_name)
	print("dataset_dir:\t",dataset_dir)
	testing_dir = ENSEMBLE_TESTING.format(
		pipeline_name=pipeline_name,
		interpreter_name=interpreter_name,
		ensemble_name=ensemble_name,
		commit_id=ensemble_last_modified
	)
	os.makedirs(testing_dir, exist_ok=True)
	
	expt = mlflow.get_experiment_by_name(pipeline_name)
	if not expt:
		mlflow.create_experiment(pipeline_name)
		expt = mlflow.get_experiment_by_name(pipeline_name)

	tb = "OK"
	with mlflow.start_run(description=testing_dir, run_name='test_'+ensemble_name, experiment_id=expt.experiment_id):
		try:
			mlflow.set_tag("COMMIT", ensemble_last_modified)
			model_predictions = {}
			for model_name in model_classes:

				model_testing_dir = MODEL_TESTING.format(
					pipeline_name=pipeline_name,
					interpreter_name=interpreter_name,
					model_name=model_name,
					commit_id=ensemble_last_modified
				)
				os.makedirs(model_testing_dir, exist_ok=True)
				results_pkl = os.path.join(model_testing_dir, "results.pkl")
				predictions_pkl = os.path.join(model_testing_dir, "predictions.pkl")
					
				results_handle = open(results_pkl, 'rb')
				results = pickle.load(results_handle)
				results_handle.close()
				predictions_handle = open(predictions_pkl, 'rb')
				predictions = pickle.load(predictions_handle)
				predictions_handle.close()
						
				model_predictions[model_name] = predictions

			dat = interpreters[interpreter_name](dataset_dir).get_dataset()
			mod = ensemble_classes[ensemble_name](testing_dir)
			#mod.predict(dat['test'])
			results, predictions = mod.evaluate(dat['test']['x'], dat['test']['y'])
			#print(results)

			results_pkl = os.path.join(testing_dir, "results.pkl")
			predictions_pkl = os.path.join(testing_dir, "predictions.pkl")
			predictions_csv = os.path.join(testing_dir, "predictions.csv")
			ensemble_pkl = os.path.join(testing_dir, "ensemble.pkl")

			results_handle = open(results_pkl, 'wb')
			pickle.dump(results, results_handle, protocol=pickle.HIGHEST_PROTOCOL)
			results_handle.close()

			predictions_handle = open(predictions_pkl, 'wb')
			pickle.dump(predictions, predictions_handle, protocol=pickle.HIGHEST_PROTOCOL)
			predictions_handle.close()

			ensemble_handle = open(ensemble_pkl, 'wb')
			pickle.dump(mod, ensemble_handle, protocol=pickle.HIGHEST_PROTOCOL)
			ensemble_handle.close()

			predictions.to_csv(predictions_csv)

			for key in results:
				mlflow.log_metric(key, results[key])
			#mlflow.log_dict(predictions)

			for visualizer_name in visualizers:
				#stat, task_id, model_last_modified, visual_dir = vizualize_ensemble(pipeline_name, model_name, interpreter_name, dataset_dir, task_id, model_last_modified, visualizers, visualizer_name, dat, 'test')
				stat, task_id, ensemble_last_modified_alt, visual_dir = vizualize_ensemble(pipeline_name, ensemble_name, interpreter_name, dataset_dir, task_id, ensemble_last_modified, visualizers, visualizer_name, dat, 'test')
				mlflow.log_artifacts(visual_dir)

			mlflow.set_tag("LOG_STATUS", "SUCCESS")
			return (True, task_id, ensemble_last_modified, task_id_source_hash, ensemble_last_source_hash)
		except KeyboardInterrupt:
			print("Interrupt recieved at ensemble_analysis")
			print("-"*10)
			print("ensemble_name:\t",ensemble_name)
			print("interpreter_name:\t",interpreter_name)
			print("dataset_dir:\t",dataset_dir)
			raise KeyboardInterrupt
		except Exception as ex:
			print(ex)
			tb = traceback.format_exc()
			mlflow.set_tag("LOG_STATUS", "FAILED")
		finally:
			print(tb)
			err_txt = os.path.join(testing_dir, "err.txt")
			err_file = open(err_txt, "w")
			err_file.write(tb)
			err_file.close()
			return (False, task_id, ensemble_last_modified, task_id_source_hash, ensemble_last_source_hash)

def main():
	loc_hist = local_history(__file__)
	task_list = {}
	all_inputs = get_all_inputs()

	for pipeline_name in all_inputs:
		all_dataset_dir = DATASET_DIR.format(pipeline_name=pipeline_name)
		interpreters = all_inputs[pipeline_name].get_pipeline_dataset_interpreter()
		for interpreter_name in interpreters:
			interpreter_dataset_dir = os.path.join(all_dataset_dir, interpreter_name)
			interpreter_datasets = glob.glob(os.path.join(interpreter_dataset_dir,"*"))
			for dataset_dir in interpreter_datasets:
				
				ensemble_classes = all_inputs[pipeline_name].get_pipeline_ensemble()
				for ensemble_name in ensemble_classes:
					
					ensemble_file_path = inspect.getfile(ensemble_classes[ensemble_name])
					#ensemble_last_modified = str(datetime.fromtimestamp(os.path.getmtime(ensemble_file_path)))
					ensemble_last_modified = str(source_hash(ensemble_classes[ensemble_name]))
					task_id = ensemble_name + ":"+ interpreter_name + ":" + dataset_dir
					
					if loc_hist[task_id] != ensemble_last_modified:
						task_list.setdefault(pipeline_name, {})
						task_list[pipeline_name].setdefault(interpreter_name, {})
						task_list[pipeline_name][interpreter_name].setdefault(dataset_dir, {})
						task_list[pipeline_name][interpreter_name][dataset_dir].setdefault(ensemble_name, (task_id, ensemble_last_modified))

	if task_list == {}:
		#print("Waiting for new tasks...")
		return

	print("-"*10)
	print("Task list:\n", json.dumps(task_list, sort_keys=True, indent=4))
	print("-"*10)

	pool_args = []

	for pipeline_name in task_list:
		all_dataset_dir = DATASET_DIR.format(pipeline_name=pipeline_name)
		interpreters = all_inputs[pipeline_name].get_pipeline_dataset_interpreter()
		for interpreter_name in task_list[pipeline_name].keys():
			interpreter_dataset_dir = os.path.join(all_dataset_dir, interpreter_name)
			interpreter_datasets = task_list[pipeline_name][interpreter_name].keys()
			for dataset_dir in interpreter_datasets:
				
				#dat = interpreters[interpreter_name](dataset_dir).get_dataset()
				ensemble_classes = all_inputs[pipeline_name].get_pipeline_ensemble()
				for ensemble_name in task_list[pipeline_name][interpreter_name][dataset_dir].keys():
					print("-"*10)
					print("ensemble_name:\t",ensemble_name)
					print("interpreter_name:\t",interpreter_name)
					print("dataset_dir:\t",dataset_dir)
					testing_dir = ENSEMBLE_TESTING.format(
						pipeline_name=pipeline_name,
						interpreter_name=interpreter_name,
						ensemble_name=ensemble_name
					)
					os.makedirs(testing_dir, exist_ok=True)
					
					task_id, ensemble_last_modified = task_list[pipeline_name][interpreter_name][dataset_dir][ensemble_name]
					
					pool_args.append((pipeline_name, ensemble_name, interpreter_name, dataset_dir, ensemble_classes, interpreters, task_id, ensemble_last_modified))

	#with torch.multiprocessing.Pool(torch.multiprocessing.cpu_count()) as p:
	with torch.multiprocessing.Pool(1) as p:
		res = p.starmap(test_ensemble, pool_args)
		for status, task_id, ensemble_last_modified in res:
			#status, task_id, ensemble_last_modified = test_ensemble(pipeline_name, ensemble_name, interpreter_name, dataset_dir, ensemble_classes, interpreters, task_id, ensemble_last_modified)
			if status:
				loc_hist[task_id] = ensemble_last_modified

if __name__ == "__main__":
	import argparse

	torch.multiprocessing.set_start_method('spawn')# good solution !!!!

	parser = argparse.ArgumentParser()
	parser.add_argument('--single', action='store_true', help='Run the loop only once')
	args = parser.parse_args()

	if args.single:
		main()
		exit()
		
	while True:
		try:
			main()
			time.sleep(5)
		except Exception as e:
			traceback.print_exc()
			print("Exception: {}".format(e))
			time.sleep(1)

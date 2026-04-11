# download_model.py
from sentence_transformers import SentenceTransformer
import os

# 1. 定义模型名称
model_name = 'paraphrase-multilingual-MiniLM-L12-v2'

print(f"正在从 HuggingFace 下载模型: {model_name} ...")
# 2. 加载模型 (这一步需要联网)
model = SentenceTransformer(model_name)

# 3. 定义保存路径 (建议保存在项目根目录下的 model 文件夹)
save_path = 'moduleE/model/paraphrase-multilingual-MiniLM-L12-v2'

# 4. 保存到本地
model.save(save_path)
print(f"模型已保存到: {os.path.abspath(save_path)}")
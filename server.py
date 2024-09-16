from fastapi import FastAPI, Request, File, UploadFile, BackgroundTasks,  HTTPException, Depends, status
from fastapi.templating import Jinja2Templates
from transformers import pipeline
from pydantic import BaseModel
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from datetime import datetime, timedelta, timezone
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
import shutil
import ocr
import os
import uuid
import json
import logging

SCRET_KEY = "fcc577289415eeb82ba7719b6bd831c1adb1c33ff4339ccbc93c2f029243f977"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30


db = {
    "sreya" : {
        "username" : "sreya",
        "full_name" : "Sreya K",
        "email" : "sreya@gmsil.com",
        "hashed_password" : "$2b$12$Iz3Rd/oCRyMXt5gqsIxUxulSw/2SykdaZ2QRiGbH9teEorMGChh5q",
        "disabled" : False
    }
}


class Token(BaseModel):
    access_token : str
    token_type : str

class TokenData(BaseModel):
    username : Optional[str] = None

class User(BaseModel):
    username : str
    email : Optional[str] = None
    full_name : Optional[str] = None
    disabled : Optional[bool] = None
    
class UserInDb(User):
    hashed_password : str
    
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")


def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def get_user(db, username:str):
    if username in db:
        user_data = db[username]
        return UserInDb(**user_data)
    

def authenticate_user(db, username:str, password:str):
    user = get_user(db, username)
    if not user:
        return False
    if not verify_password(password, user.hashed_password):
        return False
    return user


def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SCRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt



async def get_current_user(token:str=Depends(oauth2_scheme)):
    credential_exception = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials", headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = jwk.decode(token, SCRET_KEY, algorithms = [ALGORITHM])
        username:str = payload.get("sub")
        if username is None:
            raise credential_exception
        token_data = TokenData(username=username)
            
    except JWTError: 
        raise credential_exception
    
    user = get_user(username=token_data.username)
    if user is None:
        raise credential_exception
    return user

async def get_current_active_user(current_user:UserInDb=Depends(get_current_user)):
    if current_user.disabled:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user


app = FastAPI()
templates = Jinja2Templates(directory="templates")

class QuestionRequest(BaseModel):
    question: str

qa_model = pipeline("question-answering")
text_content = ""
with open("allfiles.txt", "r") as allfiles:
    text_content = allfiles.read()


def startup_tasks():
    logging.debug(f"Hashed password: {get_password_hash('sreya123')}")
    return True

@app.get("/startup")
async def startup_route(success: bool = Depends(startup_tasks)):
    return {"message": "Startup tasks executed"}

@app.get("/")
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

logging.basicConfig(level=logging.DEBUG)

@app.get("/main")
async def main_page(request: Request):
    return templates.TemplateResponse("main.html", {"request": request})


@app.post("/api/v1/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(data={"sub": user.username}, expires_delta=access_token_expires)
    return {"access_token": access_token, "token_type": "bearer"}



@app.get("/api/v1/users/me", response_model=User)
async def read_users_me(current_user: dict = Depends(get_current_active_user)):
    return current_user

pwd = get_password_hash("sreya123")
print(pwd)


@app.post("/api/v1/extract_text")
async def extract_text(image: UploadFile = File(...)):
    
    temp_file = _save_file_to_disk(image, path="temp", save_as="temp")
    text = await ocr.read_image(temp_file)
    
    file_path = os.path.abspath("allfiles.txt")

    with open(file_path, "a") as allfiles:
        allfiles.write(text + "\n")
    return {"filename": image.filename, "text": text}
 

@app.post("/api/v1/bulk_extract_text")
async def bulk_extract_text(request: Request, bg_task: BackgroundTasks):
    images = await request.form()
    folder_name = str(uuid.uuid4())
    os.mkdir(folder_name)

    for image in images.values():
        temp_file = _save_file_to_disk(image, path=folder_name, save_as=image.filename)
        text = await ocr.read_image(temp_file)
        file_path = os.path.abspath("allfiles.txt")

        with open(file_path, "a") as allfiles:
            allfiles.write(text + "\n")


    bg_task.add_task(ocr.read_images_from_dir, folder_name, write_to_file=True)
    return {"task_id": folder_name, "num_files": len(images)}

@app.get("/api/v1/bulk_output/{task_id}")
async def bulk_output(task_id):
    text_map = {}
    for file_ in os.listdir(task_id):
        if file_.endswith("txt"):
            text_map[file_] = open(os.path.join(task_id, file_)).read()            
    return {"task_id": task_id, "output": text_map}

def _save_file_to_disk(uploaded_file, path=".", save_as="default"):
    extension = os.path.splitext(uploaded_file.filename)[-1]
    temp_file = os.path.join(path, save_as + extension)
 
    with open(temp_file, "wb") as buffer:
        shutil.copyfileobj(uploaded_file.file, buffer)
    
    return temp_file


@app.post("/api/v1/ask")
async def ask_question(request: QuestionRequest):
    global text_content
    with open("allfiles.txt", "r") as allfiles:
        text_content = allfiles.read()
    if not text_content:
        raise HTTPException(status_code=400, detail="No text file uploaded yet.")

    # Use the QA model to find an answer
    result = qa_model(question=request.question, context=text_content)
    
    return {"question": request.question, "answer": result['answer']}
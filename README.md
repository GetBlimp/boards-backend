# Blimp Backend

![wercker status](https://app.wercker.com/status/975e87e15b2890a96efc090fb2664d5e "wercker status")

## Install

```
git clone git@github.com:GetBlimp/blimp-backend.git
cd blimp-backend/
pip install -r requirements.txt
```

## First time setup

```
./manage.py syncdb --noinput
./manage.py loaddata blimp/users/fixtures/users.json
```

## Running web server

```
./manage.py runserver_plus
```

## Running websockets server

```
python ws.py --debug
```

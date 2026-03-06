const API = "https://schenger.vercel.app"

const tg = window.Telegram.WebApp
tg.expand()

const user = tg.initDataUnsafe.user

document.getElementById("username").innerText =
user.first_name + " @" + user.username

document.getElementById("uid").innerText = user.id


async function load(){

let r = await fetch(API+"/user/"+user.id)
let d = await r.json()

rub.innerText = d.rub
usdt.innerText = d.usdt
ton.innerText = d.ton

}

load()


async function exchange(){

let amount = document.getElementById("amount").value
let from = document.getElementById("from").value
let to = document.getElementById("to").value

let r = await fetch(API+"/exchange",{

method:"POST",
headers:{"Content-Type":"application/json"},

body:JSON.stringify({
user_id:user.id,
amount:amount,
from:from,
to:to
})

})

let d = await r.json()

alert("Получено: "+d.received)

load()

}

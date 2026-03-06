const tg = window.Telegram.WebApp
tg.expand()

const user = tg.initDataUnsafe.user
const API = location.origin

let pages={}

function page(p){

document.querySelectorAll(".page").forEach(e=>e.style.display="none")

pages[p].style.display="block"

}

async function loadProfile(){

let r = await fetch(API+"/api/user/"+user.id)
let d = await r.json()

pages.profile.innerHTML = `
<div class="card">

<img class="avatar"
src="https://t.me/i/userpic/320/${user.username}.jpg">

<h2>${user.first_name}</h2>

<p>RUB ${d.rub}</p>
<p>USDT ${d.usdt}</p>
<p>TON ${d.ton}</p>

</div>
`
}

async function loadExchange(){

let r = await fetch(API+"/api/rates")
let rates = await r.json()

pages.exchange.innerHTML = `
<div class="card">

<h2>Обмен</h2>

<p>RUB → USDT ${rates.rub_usdt}</p>

<input id="amount">

<select id="from">
<option value="rub">RUB</option>
<option value="usdt">USDT</option>
<option value="ton">TON</option>
</select>

<select id="to">
<option value="usdt">USDT</option>
<option value="rub">RUB</option>
<option value="ton">TON</option>
</select>

<button onclick="exchange()">Обменять</button>

</div>
`
}

async function exchange(){

let amount=document.getElementById("amount").value
let from=document.getElementById("from").value
let to=document.getElementById("to").value

let r=await fetch(API+"/api/exchange",{
method:"POST",
headers:{"Content-Type":"application/json"},
body:JSON.stringify({
user_id:user.id,
amount:amount,
from:from,
to:to
})
})

let d=await r.json()

alert("Получено "+d.received)

loadProfile()

}

function loadPromo(){

pages.promo.innerHTML=`
<div class="card">

<h2>Промокод</h2>

<input placeholder="Введите промокод">

<button>Активировать</button>

</div>
`
}

function init(){

pages.profile=document.createElement("div")
pages.profile.className="page"

pages.exchange=document.createElement("div")
pages.exchange.className="page"

pages.promo=document.createElement("div")
pages.promo.className="page"

pages.admin=document.createElement("div")
pages.admin.className="page"

document.getElementById("app").append(
pages.profile,
pages.exchange,
pages.promo,
pages.admin
)

loadProfile()
loadExchange()
loadPromo()

page("profile")

}

init()
